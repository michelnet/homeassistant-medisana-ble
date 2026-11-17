"""Receive GATT indications through the Home Assistant Bluetooth adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import fields, replace
from datetime import datetime, timedelta
from struct import pack
from time import monotonic
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    BLOOD_PRESSURE_CHARACTERISTIC_UUID,
    CONF_CLOCK_BEFORE_SUBSCRIBE,
    CONF_CONNECTION_ATTEMPTS,
    CONF_CONNECTION_DELAY,
    CONF_CONNECTION_TIMEOUT,
    CONF_DEVICE_TYPE,
    CONF_HISTORY_LIMIT,
    CONF_PAIR,
    CONF_SCALE_EPOCH,
    CONF_SEPARATE_USERS,
    CONF_SYNC_TIME,
    CONF_USER_ID,
    CURRENT_TIME_UUID,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_NAME,
    DEFAULT_SCALE_CONNECTION_DELAY,
    DEVICE_INFORMATION_CHARACTERISTIC_UUIDS,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_GLUCOSE,
    DEVICE_TYPE_PULSE_OXIMETER,
    DEVICE_TYPE_SCALE,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
    EVENT_MEASUREMENT,
    GLUCOSE_CONTEXT_UUID,
    GLUCOSE_MEASUREMENT_UUID,
    LEGACY_OXIMETER_CONTROL_UUIDS,
    LEGACY_OXIMETER_MEASUREMENT_UUID,
    LEGACY_OXIMETER_SERVICE_PREFIX,
    LEGACY_OXIMETER_START,
    LEGACY_OXIMETER_WRITE_UUID,
    PLX_CONTINUOUS_UUID,
    PLX_SPOT_CHECK_UUID,
    RECORD_ACCESS_CONTROL_UUID,
    RETRY_INTERVAL,
    SCALE_BODY_UUID,
    SCALE_COMMAND_UUID,
    SCALE_USER_UUID,
    SCALE_WEIGHT_UUID,
    SESSION_TIMEOUT,
    TEMPERATURE_MEASUREMENT_UUID,
    USER_FILTER_DEVICE_TYPES,
)
from .glucose import GlucoseDecoder, GlucoseMeasurement
from .history import MeasurementHistory
from .parser import BloodPressureMeasurement, parse_blood_pressure
from .pulse_oximeter import PulseOximeterMeasurement, parse_legacy_oximeter, parse_plx
from .scale import (
    ScaleDecoder,
    ScaleMeasurement,
    default_scale_epoch,
    scale_time_command,
)
from .thermometer import TemperatureMeasurement, parse_temperature_measurement

_LOGGER = logging.getLogger(__name__)
_BLOOD_PRESSURE_VALUE_KEYS = frozenset(
    {"systolic", "diastolic", "mean_arterial_pressure", "pulse"}
)
type MedisanaMeasurement = (
    BloodPressureMeasurement
    | TemperatureMeasurement
    | ScaleMeasurement
    | GlucoseMeasurement
    | PulseOximeterMeasurement
)


class MedisanaBleakClient(BleakClientWithServiceCache):
    """Enforce the requested timeout for each retry on all BLE backends."""

    def __init__(self, *args, timeout: float = 20, **kwargs) -> None:
        super().__init__(*args, timeout=timeout, **kwargs)
        self._connection_timeout = timeout

    async def connect(self, **kwargs) -> None:
        # The retry connector passes its own fixed 20-second timeout to connect.
        # Override it and bound the entire attempt, including service discovery.
        kwargs["timeout"] = self._connection_timeout
        try:
            async with asyncio.timeout(self._connection_timeout):
                await super().connect(**kwargs)
        except BaseException:
            # The connector cannot hand the client back if pairing raises an
            # unsupported-backend error. Also clean up timed-out/cancelled
            # attempts here before the connector starts another attempt.
            with suppress(BleakError, OSError, TimeoutError, asyncio.CancelledError):
                async with asyncio.timeout(5):
                    await self.disconnect()
            raise


class MedisanaCoordinator(DataUpdateCoordinator[MedisanaMeasurement]):
    """Keep the last reading; a sleeping instrument is normal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=entry.title)
        self.address: str = entry.data[CONF_ADDRESS]
        self.device_type: str = entry.data.get(
            CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE
        )
        self.user_id_filter: int = entry.options.get(CONF_USER_ID, -1)
        self.sync_time: bool = entry.options.get(CONF_SYNC_TIME, False)
        self.scale_epoch = entry.options.get(
            CONF_SCALE_EPOCH,
            entry.data.get(CONF_SCALE_EPOCH, default_scale_epoch(entry.title)),
        )
        self._scale_decoder = ScaleDecoder(self.scale_epoch)
        self.scale_connection_delay = entry.options.get(
            CONF_CONNECTION_DELAY, DEFAULT_SCALE_CONNECTION_DELAY
        )
        self._glucose_decoder = GlucoseDecoder()
        self.connection_timeout = entry.options.get(CONF_CONNECTION_TIMEOUT, 20)
        self.connection_attempts = entry.options.get(CONF_CONNECTION_ATTEMPTS, 2)
        self.pair = entry.options.get(CONF_PAIR, False)
        self.clock_before_subscribe = entry.options.get(
            CONF_CLOCK_BEFORE_SUBSCRIBE, False
        )
        self.separate_users = (
            self.device_type in USER_FILTER_DEVICE_TYPES
            and entry.options.get(CONF_SEPARATE_USERS, False)
        )
        self.history = MeasurementHistory(
            hass,
            entry.entry_id,
            entry.options.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT),
        )
        self.user_measurements: dict[int, MedisanaMeasurement] = {}
        self.user_received_at: dict[int, datetime] = {}
        self._restored_user_values: dict[int, dict] = {}
        self.connection_diagnostics = {
            "sessions": 0,
            "connections": 0,
            "failures": 0,
            "malformed_packets": 0,
            "last_error": None,
        }
        self.gatt_endpoints: list[dict] = []
        self.oximeter_protocol: str | None = None
        self.received_at: datetime | None = None
        self.measurement_sequence = 0
        self.latest_timestamp: datetime | None = None
        self._restored_timestamp: datetime | None = None
        self._restored_values: dict[str, Any] = {}
        self.battery_level: int | None = None
        self.device_info: dict[str, str] = {}
        self._entry = entry
        self._task: asyncio.Task | None = None
        self._client: BleakClient | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._next_attempt = 0.0

    async def async_initialize(self) -> None:
        """Restore per-user readings from the private bounded record store."""
        await self.history.async_load()
        if self.device_type not in USER_FILTER_DEVICE_TYPES:
            return
        cls = (
            ScaleMeasurement
            if self.device_type == DEVICE_TYPE_SCALE
            else BloodPressureMeasurement
        )
        for record in self.history.records.values():
            values = {
                field.name: record[field.name]
                for field in fields(cls)
                if field.name in record
            }
            if values.get("timestamp"):
                values["timestamp"] = datetime.fromisoformat(values["timestamp"])
                if cls is BloodPressureMeasurement:
                    values["timestamp"] = (
                        values["timestamp"]
                        .astimezone(dt_util.get_default_time_zone())
                        .replace(tzinfo=None)
                    )
            measurement = cls(**values)
            self._remember_user(
                measurement, datetime.fromisoformat(record["received_at"])
            )

    @callback
    def _remember_user(self, measurement, received_at: datetime) -> bool:
        """Keep each known user independent of the main sensor's user filter."""
        if not self.separate_users:
            return False
        user = getattr(measurement, "user_id", None)
        if user is None or (self.device_type == DEVICE_TYPE_SCALE and user == 0):
            return False
        previous = self._previous_user_measurement(user)
        if previous is not None:
            if (
                previous.timestamp is not None
                and measurement.timestamp is not None
                and measurement.timestamp < previous.timestamp
            ):
                return False
            if measurement == previous and measurement.timestamp is not None:
                return False
        self.user_measurements[user] = measurement
        self._restored_user_values.pop(user, None)
        self.user_received_at[user] = received_at
        return True

    @callback
    def restore_user_sensor_data(
        self, user: int, key: str, value, unit, attributes
    ) -> None:
        """Seed replay protection from entity restore when history is disabled."""
        cls = (
            ScaleMeasurement
            if self.device_type == DEVICE_TYPE_SCALE
            else BloodPressureMeasurement
        )
        values = self._restored_user_values.setdefault(user, {})
        if key in {field.name for field in fields(cls)}:
            values[key] = value
        if key in _BLOOD_PRESSURE_VALUE_KEYS and unit in ("mmHg", "kPa"):
            values["pressure_unit"] = unit
        for name in ("measurement_time", "measurement_status", "raw_user_id"):
            if name in attributes:
                values[name] = attributes[name]

    def _previous_user_measurement(self, user: int | None):
        if previous := self.user_measurements.get(user):
            return previous
        if user not in self._restored_user_values:
            return None
        restored = self._restored_user_values[user]
        timestamp = restored.get("measurement_time")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if timestamp is None:
            return None
        if self.device_type == DEVICE_TYPE_BLOOD_PRESSURE:
            timestamp = timestamp.astimezone(dt_util.get_default_time_zone()).replace(
                tzinfo=None
            )
            cls = BloodPressureMeasurement
        else:
            if restored.get("weight") is None:
                return None
            cls = ScaleMeasurement
        values = {field.name: restored.get(field.name) for field in fields(cls)}
        values.update(user_id=user, timestamp=timestamp)
        if cls is BloodPressureMeasurement:
            values["pressure_unit"] = restored.get("pressure_unit", "mmHg")
        else:
            values["raw_user_id"] = restored.get("raw_user_id", user)
        return cls(**values)

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

        self.connection_diagnostics["sessions"] += 1
        try:
            async with asyncio.timeout(SESSION_TIMEOUT):
                if self.device_type == DEVICE_TYPE_SCALE:
                    # BS 444 advertises while composition is still being
                    # computed. Let it finish before initiating a connection.
                    await asyncio.sleep(self.scale_connection_delay)
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if device is None:
                    return
                self._client = await establish_connection(
                    MedisanaBleakClient,
                    device,
                    self.name,
                    disconnected_callback=on_disconnect,
                    max_attempts=self.connection_attempts,
                    timeout=float(self.connection_timeout),
                    **({"pair": True} if self.pair else {}),
                )
                if not self._client.is_connected:
                    return
                self.connection_diagnostics["connections"] += 1
                self.connection_diagnostics["last_error"] = None
                self.gatt_endpoints = [
                    {
                        "service": service.uuid,
                        "characteristic": characteristic.uuid,
                        "properties": list(characteristic.properties),
                    }
                    for service in self._client.services
                    for characteristic in service.characteristics
                ]
                self._scale_decoder = ScaleDecoder(self.scale_epoch)
                self._glucose_decoder = GlucoseDecoder()
                if not await self._async_subscribe():
                    self.connection_diagnostics["last_error"] = (
                        "unsupported_gatt_profile"
                    )
                    return
                await self._async_read_optional_device_info()
                self.async_update_listeners()
                await disconnected.wait()
        except (BleakError, OSError, TimeoutError, NotImplementedError) as err:
            self.connection_diagnostics["failures"] += 1
            self.connection_diagnostics["last_error"] = type(err).__name__
            # No raw packets or health data in the logs. Sleeping devices and
            # occupied connection slots are expected; the next advertisement retries.
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

    def _notifiable_characteristic(self, uuid: str):
        """Use only endpoints that actually support push measurements."""
        characteristic = self._client.services.get_characteristic(uuid)
        if characteristic is not None and {"notify", "indicate"} & set(
            characteristic.properties
        ):
            return characteristic
        return None

    async def _async_subscribe(self) -> bool:
        """Subscribe before any reads or commands that can trigger a transfer."""
        if self.device_type == DEVICE_TYPE_SCALE:
            weight = self._notifiable_characteristic(SCALE_WEIGHT_UUID)
            command = self._client.services.get_characteristic(SCALE_COMMAND_UUID)
            if (
                weight is None
                or command is None
                or not {"write", "write-without-response"} & set(command.properties)
            ):
                return False
            await self._client.start_notify(weight, self._notification)
            for uuid in (SCALE_BODY_UUID, SCALE_USER_UUID):
                if characteristic := self._notifiable_characteristic(uuid):
                    await asyncio.sleep(0.3)
                    await self._client.start_notify(characteristic, self._notification)
            await asyncio.sleep(0.3)
            await self._client.write_gatt_char(
                command,
                scale_time_command(dt_util.utcnow(), self.scale_epoch),
                response="write" in command.properties,
            )
            return True
        if self.device_type == DEVICE_TYPE_PULSE_OXIMETER:
            subscribed = False
            for uuid in (PLX_SPOT_CHECK_UUID, PLX_CONTINUOUS_UUID):
                if characteristic := self._notifiable_characteristic(uuid):
                    await self._client.start_notify(characteristic, self._notification)
                    subscribed = True
            if subscribed:
                self.oximeter_protocol = "sig_plx"
                return True
            return await self._async_subscribe_legacy_oximeter()
        uuid = {
            DEVICE_TYPE_BLOOD_PRESSURE: BLOOD_PRESSURE_CHARACTERISTIC_UUID,
            DEVICE_TYPE_THERMOMETER: TEMPERATURE_MEASUREMENT_UUID,
            DEVICE_TYPE_GLUCOSE: GLUCOSE_MEASUREMENT_UUID,
        }.get(self.device_type)
        if (
            uuid is None
            or (characteristic := self._notifiable_characteristic(uuid)) is None
        ):
            _LOGGER.debug("Configured measurement profile is not available")
            return False
        if (
            self.device_type == DEVICE_TYPE_BLOOD_PRESSURE
            and self.sync_time
            and self.clock_before_subscribe
        ):
            await self._async_sync_clock()
        await self._client.start_notify(characteristic, self._notification)
        if (
            self.device_type == DEVICE_TYPE_BLOOD_PRESSURE
            and self.sync_time
            and not self.clock_before_subscribe
        ):
            await self._async_sync_clock()
        if self.device_type == DEVICE_TYPE_GLUCOSE:
            if context := self._notifiable_characteristic(GLUCOSE_CONTEXT_UUID):
                await self._client.start_notify(context, self._notification)
            # Some MediTouch devices automatically send records without RACP.
            # For standard devices, request records only after all callbacks
            # are ready. Opcode 01 reports records; no delete command is used.
            if (
                racp := self._notifiable_characteristic(RECORD_ACCESS_CONTROL_UUID)
            ) is not None and "write" in racp.properties:
                try:
                    await self._client.start_notify(racp, self._racp_notification)
                    await self._client.write_gatt_char(racp, b"\x01\x01", response=True)
                except BleakError, OSError, TimeoutError:
                    _LOGGER.debug("Stored glucose records could not be requested")
        return True

    async def _async_subscribe_legacy_oximeter(self) -> bool:
        """Initialize only a matching legacy service with all five endpoints."""
        for service in self._client.services:
            if not service.uuid.lower().startswith(LEGACY_OXIMETER_SERVICE_PREFIX):
                continue
            chars = {char.uuid.lower(): char for char in service.characteristics}
            command = chars.get(LEGACY_OXIMETER_WRITE_UUID)
            notify_uuids = (
                *LEGACY_OXIMETER_CONTROL_UUIDS,
                LEGACY_OXIMETER_MEASUREMENT_UUID,
            )
            if (
                command is None
                or not {"write", "write-without-response"} & set(command.properties)
                or any(
                    uuid not in chars
                    or not {"notify", "indicate"} & set(chars[uuid].properties)
                    for uuid in notify_uuids
                )
            ):
                continue
            for uuid in notify_uuids:
                await self._client.start_notify(
                    chars[uuid],
                    self._notification
                    if uuid == LEGACY_OXIMETER_MEASUREMENT_UUID
                    else self._legacy_control_notification,
                )
            await self._client.write_gatt_char(
                command,
                LEGACY_OXIMETER_START,
                response="write" in command.properties,
            )
            self.oximeter_protocol = "legacy_cd04"
            return True
        return False

    def _legacy_control_notification(self, characteristic, data: bytearray) -> None:
        """Control acknowledgements contain no decoded measurement values."""

    async def _async_sync_clock(self) -> None:
        """Optionally supply Current Time using the configured device sequence."""
        characteristic = self._client.services.get_characteristic(CURRENT_TIME_UUID)
        if characteristic is None or not {"write", "write-without-response"} & set(
            characteristic.properties
        ):
            return
        now = dt_util.utcnow().astimezone(dt_util.get_default_time_zone())
        # Bluetooth Current Time: Exact Time 256 followed by Adjust Reason.
        value = pack(
            "<H8B",
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            now.isoweekday(),
            0,
            2,
        )
        try:
            await self._client.write_gatt_char(
                characteristic, value, response="write" in characteristic.properties
            )
        except BleakError, OSError, TimeoutError:
            _LOGGER.debug("Device clock could not be synchronized")

    def _racp_notification(self, characteristic, data: bytearray) -> None:
        """RACP acknowledgements carry no measurements; keep receiving live data."""

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
                _LOGGER.debug("Characteristic missing UUID; skipping")
                return None
            if expected_uuid is not None:
                if char_uuid.lower() != expected_uuid.lower():
                    _LOGGER.debug("UUID mismatch for %s; skipping", expected_uuid)
                    return None
            elif char_uuid.lower() not in supported_uuids:
                _LOGGER.debug("Unsupported UUID; skipping")
                return None
            try:
                value = self._client.read_gatt_char(char)
            except AttributeError, TypeError:
                _LOGGER.debug("Characteristic read failed; skipping")
                return None
            if value is None or not inspect.isawaitable(value):
                _LOGGER.debug("Read returned non-waitable value; skipping")
                return None
            try:
                return await asyncio.wait_for(value, timeout=5)
            except TypeError:
                _LOGGER.debug("Timeout reading characteristic; skipping")
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
        uuid = getattr(characteristic, "uuid", None)
        self.hass.loop.call_soon_threadsafe(
            self._async_handle_packet,
            bytes(data),
            uuid.lower() if isinstance(uuid, str) else None,
        )

    @callback
    def restore_sensor_data(
        self,
        key: str,
        native_value: Any,
        native_unit: str | None,
        attributes: Mapping[str, Any],
    ) -> None:
        """Seed blood-pressure replay detection from restored sensor state."""
        if (
            self.device_type != DEVICE_TYPE_BLOOD_PRESSURE
            or self.hass.state is CoreState.running
        ):
            return
        value = attributes.get("measurement_time")
        if value is None:
            return
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return
        elif not isinstance(value, datetime):
            return
        if value.tzinfo is not None:
            value = value.astimezone(dt_util.get_default_time_zone()).replace(
                tzinfo=None
            )
        if self._restored_timestamp is not None and value < self._restored_timestamp:
            return
        if self._restored_timestamp is None or value > self._restored_timestamp:
            self._restored_values.clear()
            self._restored_timestamp = value
        self.latest_timestamp = value

        measurement_keys = {*_BLOOD_PRESSURE_VALUE_KEYS, "user_id"}
        if key in measurement_keys:
            self._restored_values[key] = native_value
        if key in {"systolic", "diastolic", "mean_arterial_pressure"}:
            self._restored_values["pressure_unit"] = native_unit
        for attribute in ("user_id", "measurement_status"):
            if attribute in attributes:
                self._restored_values[attribute] = attributes[attribute]

    def _matches_restored_blood_pressure(
        self, measurement: BloodPressureMeasurement
    ) -> bool:
        """Return whether a packet is the measurement restored at startup."""
        if measurement.timestamp != self._restored_timestamp:
            return False
        compared_value = False
        for key, value in self._restored_values.items():
            compared_value |= key in _BLOOD_PRESSURE_VALUE_KEYS
            if getattr(measurement, key) != value:
                return False
        return compared_value

    @callback
    def _async_handle_packet(self, data: bytes, uuid: str | None = None) -> None:
        """Publish complete, validated measurements immediately."""
        if self._stopped:
            return
        try:
            if self.device_type == DEVICE_TYPE_THERMOMETER:
                measurement = parse_temperature_measurement(data)
            elif self.device_type == DEVICE_TYPE_PULSE_OXIMETER:
                measurement = (
                    parse_legacy_oximeter(data)
                    if uuid == LEGACY_OXIMETER_MEASUREMENT_UUID
                    else parse_plx(data, continuous=uuid == PLX_CONTINUOUS_UUID)
                )
            elif self.device_type == DEVICE_TYPE_GLUCOSE:
                measurement = (
                    self._glucose_decoder.context(data)
                    if uuid == GLUCOSE_CONTEXT_UUID
                    else self._glucose_decoder.measurement(data)
                )
            elif self.device_type == DEVICE_TYPE_SCALE:
                measurement = self._scale_decoder.feed(uuid or SCALE_WEIGHT_UUID, data)
            elif self.device_type == DEVICE_TYPE_BLOOD_PRESSURE:
                measurement = parse_blood_pressure(data)
            else:
                return
        except ValueError:
            self.connection_diagnostics["malformed_packets"] += 1
            _LOGGER.debug("Ignoring malformed measurement packet (%d bytes)", len(data))
            return
        if measurement is None:
            return
        received_at = dt_util.utcnow()
        # A replayed weight frame must not erase already joined composition.
        if isinstance(measurement, ScaleMeasurement):
            candidates = [
                self.data,
                self._previous_user_measurement(measurement.user_id),
            ]
            for previous in candidates:
                if isinstance(previous, ScaleMeasurement) and (
                    previous.timestamp,
                    previous.user_id,
                    previous.raw_user_id,
                    previous.weight,
                ) == (
                    measurement.timestamp,
                    measurement.user_id,
                    measurement.raw_user_id,
                    measurement.weight,
                ):
                    measurement = replace(
                        measurement,
                        **{
                            field.name: getattr(previous, field.name)
                            for field in fields(ScaleMeasurement)
                            if getattr(measurement, field.name) is None
                        },
                    )
                    break
        record = self.history.record(self.device_type, measurement, received_at)
        user_updated = self._remember_user(measurement, received_at)
        if record is not None:
            self.hass.bus.async_fire(
                EVENT_MEASUREMENT,
                {
                    "entry_id": self._entry.entry_id,
                    "device_type": self.device_type,
                    "measurement": record,
                },
            )
        if user_updated:
            self.async_update_listeners()
        if (
            self.device_type in USER_FILTER_DEVICE_TYPES
            and self.user_id_filter != -1
            and measurement.user_id != self.user_id_filter
        ):
            return
        if isinstance(measurement, GlucoseMeasurement):
            if isinstance(self.data, GlucoseMeasurement) and (
                measurement.sequence_number < self.data.sequence_number
                or measurement == self.data
            ):
                return
        elif isinstance(measurement, ScaleMeasurement) and measurement == self.data:
            return
        # A memory sync may send newest records first. For blood-pressure
        # monitors, the restored timestamp also prevents the last stored record
        # from being saved again after Home Assistant restarts.
        if (
            self.latest_timestamp is not None
            and not isinstance(measurement, GlucoseMeasurement)
            and measurement.timestamp is not None
            and measurement.timestamp < self.latest_timestamp
        ):
            return
        if (
            isinstance(measurement, BloodPressureMeasurement)
            and measurement.timestamp is not None
            and (
                self.data == measurement
                or (
                    self.data is None
                    and self._matches_restored_blood_pressure(measurement)
                )
            )
        ):
            return
        # Every indication is a measurement event. The TM 750 can produce the
        # same value and timestamp again, so payload equality cannot reliably
        # distinguish a new measurement from a retransmission.
        self.measurement_sequence += 1
        self.received_at = received_at
        if measurement.timestamp is not None and (
            self.latest_timestamp is None
            or measurement.timestamp > self.latest_timestamp
        ):
            self.latest_timestamp = measurement.timestamp
            self._restored_timestamp = None
            self._restored_values.clear()
        self.async_set_updated_data(measurement)

    async def async_stop(self) -> None:
        """Cancel callbacks, timer and any connection, also during shutdown."""
        if self._stopped:
            return
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
        await self.history.async_flush()
