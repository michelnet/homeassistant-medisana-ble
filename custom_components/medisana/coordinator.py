"""Receive GATT indications through the Home Assistant Bluetooth adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from time import monotonic

from bleak import BleakClient, BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    BLOOD_PRESSURE_CHARACTERISTIC_UUID,
    CONF_DEVICE_TYPE,
    CONF_USER_ID,
    DEFAULT_NAME,
    DEVICE_INFORMATION_CHARACTERISTIC_UUIDS,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
    RETRY_INTERVAL,
    SESSION_TIMEOUT,
    TEMPERATURE_MEASUREMENT_UUID,
)
from .parser import BloodPressureMeasurement, parse_blood_pressure
from .thermometer import TemperatureMeasurement, parse_temperature_measurement

_LOGGER = logging.getLogger(__name__)


class MedisanaCoordinator(
    DataUpdateCoordinator[BloodPressureMeasurement | TemperatureMeasurement]
):
    """Keep the last reading; a sleeping instrument is normal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=entry.title)
        self.address: str = entry.data[CONF_ADDRESS]
        self.device_type: str = entry.data.get(
            CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE
        )
        self.user_id_filter: int = entry.options.get(CONF_USER_ID, -1)
        self.received_at: datetime | None = None
        self.battery_level: int | None = None
        self.device_info: dict[str, str] = {}
        self._entry = entry
        self._task: asyncio.Task | None = None
        self._client: BleakClient | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._next_attempt = 0.0

    @callback
    def async_start(self) -> None:
        """Listen for wakeups and retry unchanged advertisements too."""
        self._unsubscribers.append(
            bluetooth.async_register_callback(
                self.hass,
                self._async_discovered,
                {"address": self.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        # Some instruments advertise identical bytes on every wakeup. A timer
        # also handles those because discovery callbacks may suppress repeats.
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass, self._async_maybe_connect, timedelta(seconds=RETRY_INTERVAL)
            )
        )
        self._entry.async_on_unload(self.async_stop)
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_hass_stop
            )
        )
        self._async_maybe_connect()

    async def _async_hass_stop(self, event: Event) -> None:
        """Release the connection before Home Assistant shuts down."""
        await self.async_stop()

    @callback
    def _async_discovered(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self._async_maybe_connect()

    @callback
    def _async_maybe_connect(self, now: datetime | None = None) -> None:
        """Start at most one bounded session for a reachable device."""
        if (
            self._stopped
            or self._task is not None
            or monotonic() < self._next_attempt
            or not bluetooth.async_address_present(self.hass, self.address, True)
        ):
            return
        self._task = self.hass.async_create_background_task(
            self._async_listen(), f"{DEFAULT_NAME} {self.address}", eager_start=False
        )

    async def _async_listen(self) -> None:
        """Subscribe to final measurements until disconnect or session timeout."""
        disconnected = asyncio.Event()

        def on_disconnect(client: BleakClient) -> None:
            # The retry connector can invoke this for a failed attempt before
            # returning a successful connection using the same client object.
            if client is self._client:
                self.hass.loop.call_soon_threadsafe(disconnected.set)

        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                return
            async with asyncio.timeout(SESSION_TIMEOUT):
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.name,
                    disconnected_callback=on_disconnect,
                    max_attempts=2,
                    timeout=20.0,
                )
                if not self._client.is_connected:
                    return
                uuid = (
                    TEMPERATURE_MEASUREMENT_UUID
                    if self.device_type == DEVICE_TYPE_THERMOMETER
                    else BLOOD_PRESSURE_CHARACTERISTIC_UUID
                )
                characteristic = self._client.services.get_characteristic(uuid)
                if characteristic is None or not (
                    {"notify", "indicate"} & set(characteristic.properties)
                ):
                    _LOGGER.warning(
                        "Configured device does not expose the expected measurement "
                        "characteristic %s with notifications/indications; "
                        "check the selected Medisana device type",
                        uuid,
                    )
                    return
                # Subscribe before optional reads so a short-lived measurement
                # is received while battery and device details are being read.
                # Bleak start_notify also enables indications (2A35 and 2A1C).
                await self._client.start_notify(characteristic, self._notification)
                await self._async_read_optional_device_info()
                self.async_update_listeners()
                await disconnected.wait()
        except BleakError, OSError, TimeoutError:
            # No raw packets or health data in the logs. Sleeping devices and
            # Occupied connection slots are expected; the next advertisement retries.
            _LOGGER.debug("Bluetooth session ended or could not connect")
        finally:
            if self._client is not None:
                try:
                    async with asyncio.timeout(10):
                        await self._client.disconnect()
                except BleakError, OSError, TimeoutError:
                    _LOGGER.debug("Bluetooth disconnect did not complete")
                self._client = None
            self._next_attempt = monotonic() + RETRY_INTERVAL
            self._task = None

    async def _async_read_optional_device_info(self) -> None:
        """Read optional battery and device information when a monitor exposes it."""
        if self._client is None or not self._client.is_connected:
            return

        supported_uuids = {
            BATTERY_LEVEL_CHARACTERISTIC_UUID.lower(),
            *{
                uuid.lower()
                for uuid in DEVICE_INFORMATION_CHARACTERISTIC_UUIDS.values()
            },
        }

        async def _read_characteristic(char, expected_uuid: str | None = None):
            char_uuid = getattr(char, "uuid", None)
            if not isinstance(char_uuid, str):
                return None
            if expected_uuid is not None:
                if char_uuid.lower() != expected_uuid.lower():
                    return None
            elif char_uuid.lower() not in supported_uuids:
                return None
            try:
                value = self._client.read_gatt_char(char)
            except AttributeError, TypeError:
                return None
            if value is None or not inspect.isawaitable(value):
                return None
            try:
                return await asyncio.wait_for(value, timeout=5)
            except TypeError:
                return None

        battery_characteristic = self._client.services.get_characteristic(
            BATTERY_LEVEL_CHARACTERISTIC_UUID
        )
        if battery_characteristic is not None:
            try:
                raw = await _read_characteristic(
                    battery_characteristic, BATTERY_LEVEL_CHARACTERISTIC_UUID
                )
            except BleakError, OSError, TimeoutError:
                _LOGGER.debug("Battery level could not be read", exc_info=True)
            else:
                if raw and len(raw) == 1 and 0 <= raw[0] <= 100:
                    self.battery_level = raw[0]

        for key, uuid in DEVICE_INFORMATION_CHARACTERISTIC_UUIDS.items():
            characteristic = self._client.services.get_characteristic(uuid)
            if characteristic is None:
                continue
            try:
                raw = await _read_characteristic(characteristic, uuid)
            except BleakError, OSError, TimeoutError:
                _LOGGER.debug("Device info %s could not be read", key, exc_info=True)
                continue
            text = raw.decode("utf-8", errors="replace").strip() if raw else ""
            if text:
                self.device_info[key] = text

        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self.address)}):
            fields = {
                "manufacturer_name": "manufacturer",
                "model_number": "model",
                "firmware_revision": "sw_version",
                "serial_number": "serial_number",
                "hardware_revision": "hw_version",
            }
            registry.async_update_device(
                device.id,
                **{fields[key]: value for key, value in self.device_info.items()},
            )

    def _notification(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Copy the packet before dispatching it to Home Assistant's loop."""
        self.hass.loop.call_soon_threadsafe(self._async_handle_packet, bytes(data))

    @callback
    def _async_handle_packet(self, data: bytes) -> None:
        """Publish complete, validated measurements immediately."""
        if self._stopped:
            return
        try:
            measurement = (
                parse_temperature_measurement(data)
                if self.device_type == DEVICE_TYPE_THERMOMETER
                else parse_blood_pressure(data)
            )
        except ValueError:
            _LOGGER.debug("Ignoring malformed measurement packet (%d bytes)", len(data))
            return
        if (
            isinstance(measurement, BloodPressureMeasurement)
            and self.user_id_filter != -1
            and measurement.user_id != self.user_id_filter
        ):
            return
        # A memory sync may send newest records first. Never replace a newer
        # timestamp with an older one within this running coordinator.
        if (
            self.data is not None
            and self.data.timestamp is not None
            and measurement.timestamp is not None
            and measurement.timestamp < self.data.timestamp
        ):
            return
        if self.data == measurement and measurement.timestamp is not None:
            return
        self.received_at = dt_util.utcnow()
        self.async_set_updated_data(measurement)

    async def async_stop(self) -> None:
        """Cancel callbacks, timer and any connection, also during shutdown."""
        self._stopped = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        task = self._task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            # The coroutine's finally block cannot run if it was cancelled
            # before its first turn on the event loop.
            self._task = None
