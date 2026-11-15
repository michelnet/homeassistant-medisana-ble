"""Discover and configure supported Medisana BLE profiles."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BLOOD_PRESSURE_SERVICE_UUID,
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
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_NAME,
    DEFAULT_SCALE_CONNECTION_DELAY,
    DEVICE_NAMES,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_GLUCOSE,
    DEVICE_TYPE_PULSE_OXIMETER,
    DEVICE_TYPE_SCALE,
    DEVICE_TYPE_THERMOMETER,
    DISCOVERY_LOCAL_NAMES,
    DISCOVERY_MANUFACTURER_IDS,
    DOMAIN,
    GLUCOSE_SERVICE_UUID,
    HEALTH_THERMOMETER_SERVICE_UUID,
    LEGACY_OXIMETER_SERVICE_PREFIX,
    PULSE_OXIMETER_SERVICE_UUID,
    SCALE_SERVICE_UUID,
    THERMOMETER_LOCAL_NAMES,
    USER_FILTER_DEVICE_TYPES,
)
from .scale import SCALE_EPOCHS, default_scale_epoch

_MAC_ADDRESS = re.compile(
    r"(?:[0-9a-f]{12}|(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|"
    r"(?:[0-9a-f]{2}-){5}[0-9a-f]{2})",
    re.IGNORECASE,
)


def _normalize_address(address: str) -> str:
    """Validate a Bluetooth MAC and return its canonical uppercase form."""
    address = address.strip()
    if not _MAC_ADDRESS.fullmatch(address):
        raise ValueError("Invalid Bluetooth MAC address")
    compact = address.replace(":", "").replace("-", "").upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def _discovery_address(address: str) -> str:
    """Also retain CoreBluetooth's stable device UUIDs on macOS."""
    try:
        return _normalize_address(address)
    except ValueError:
        return address


def get_device_type(info: BluetoothServiceInfoBleak) -> str | None:
    """Classify connectable devices using their advertised services or names."""
    if not info.connectable:
        return None
    service_uuids = {uuid.lower() for uuid in info.service_uuids}
    if SCALE_SERVICE_UUID in service_uuids:
        # The Yolanda WLAN setup endpoint shares the advertisement UUID, but
        # does not transfer measurements through this classic scale protocol.
        if (info.name or "").startswith("000000"):
            return None
        return DEVICE_TYPE_SCALE
    if PULSE_OXIMETER_SERVICE_UUID in service_uuids or (
        "sugar" not in (info.name or "").casefold()
        and any(
            uuid.startswith(LEGACY_OXIMETER_SERVICE_PREFIX) for uuid in service_uuids
        )
    ):
        return DEVICE_TYPE_PULSE_OXIMETER
    if GLUCOSE_SERVICE_UUID in service_uuids:
        return DEVICE_TYPE_GLUCOSE
    if (
        HEALTH_THERMOMETER_SERVICE_UUID in service_uuids
        or info.name in THERMOMETER_LOCAL_NAMES
    ):
        return DEVICE_TYPE_THERMOMETER
    if (
        bool(DISCOVERY_MANUFACTURER_IDS.intersection(info.manufacturer_data))
        or info.name in DISCOVERY_LOCAL_NAMES
        or BLOOD_PRESSURE_SERVICE_UUID in service_uuids
    ):
        return DEVICE_TYPE_BLOOD_PRESSURE
    return None


def is_supported_discovery(info: BluetoothServiceInfoBleak) -> bool:
    """Return whether discovery identifies a supported Medisana device type."""
    return get_device_type(info) is not None


def _discovery_name(info: BluetoothServiceInfoBleak) -> str:
    """Show a friendly name with an address suffix for multiple monitors."""
    default_name = DEVICE_NAMES[get_device_type(info)]
    name = info.name
    if not name or name == info.address or name in (DEFAULT_NAME, default_name):
        return f"{default_name} ({info.address[-5:]})"
    return f"{default_name} ({name}, {info.address[-5:]})"


class MedisanaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Offer automatic discovery with a manual fallback for sleeping devices."""

    VERSION = 1

    def __init__(self) -> None:
        self._address = ""
        self._name = ""
        self._device_type = DEVICE_TYPE_BLOOD_PRESSURE
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MedisanaOptionsFlow:
        return MedisanaOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Every profile offers local retention and connection settings."""
        return True

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Create the discovered-device card without asking for a MAC or type."""
        device_type = get_device_type(discovery_info)
        if device_type is None:
            return self.async_abort(reason="not_supported")
        self._device_type = device_type
        self._address = _discovery_address(discovery_info.address)
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        self._name = _discovery_name(discovery_info)
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery, then choose the clock variant for a classic scale."""
        if user_input is not None:
            self._abort_if_unique_id_configured()
            if self._device_type == DEVICE_TYPE_SCALE:
                return await self.async_step_scale()
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_ADDRESS: self._address,
                    CONF_DEVICE_TYPE: self._device_type,
                },
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name, "address": self._address},
        )

    async def async_step_scale(self, user_input=None) -> ConfigFlowResult:
        """Choose the scale clock variant before any RTC write is possible."""
        if user_input is not None:
            if user_input.get(CONF_SCALE_EPOCH) not in SCALE_EPOCHS:
                return self.async_abort(reason="not_supported")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_ADDRESS: self._address,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SCALE,
                    CONF_SCALE_EPOCH: user_input[CONF_SCALE_EPOCH],
                },
            )
        return self.async_show_form(
            step_id="scale",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCALE_EPOCH): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(SCALE_EPOCHS), translation_key=CONF_SCALE_EPOCH
                        )
                    ),
                }
            ),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find nearby devices, with an address fallback if they are asleep."""
        return self.async_show_menu(
            step_id="user", menu_options=["discovered", "manual"]
        )

    async def async_step_discovered(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List already discovered devices without initiating a GATT connection."""
        if user_input is not None:
            return await self.async_step_bluetooth(
                self._discovered[user_input[CONF_ADDRESS]]
            )
        configured = self._async_current_ids()
        self._discovered = {
            _discovery_address(info.address): info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if is_supported_discovery(info)
            and _discovery_address(info.address) not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="discovered",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: _discovery_name(info)
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow setup of a sleeping monitor using its known Bluetooth address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = _normalize_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "invalid_name"
            if (
                user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE)
                not in DEVICE_NAMES
            ):
                errors[CONF_DEVICE_TYPE] = "invalid_device_type"
            if not errors:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                if user_input.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_SCALE:
                    self._address = address
                    self._name = name
                    self._device_type = DEVICE_TYPE_SCALE
                    return await self.async_step_scale()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_ADDRESS: address,
                        CONF_DEVICE_TYPE: user_input.get(
                            CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE
                        ),
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): selector.TextSelector(),
                vol.Required(
                    CONF_DEVICE_TYPE, default=DEVICE_TYPE_BLOOD_PRESSURE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DEVICE_NAMES),
                        translation_key=CONF_DEVICE_TYPE,
                    )
                ),
                vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class MedisanaOptionsFlow(OptionsFlow):
    """Configure local retention, connection timing and optional user groups."""

    def _settings(self) -> dict[str, tuple[int, int, int]]:
        settings = {
            CONF_CONNECTION_TIMEOUT: (3, 60, 20),
            CONF_CONNECTION_ATTEMPTS: (1, 10, 2),
            CONF_HISTORY_LIMIT: (0, 1000, DEFAULT_HISTORY_LIMIT),
        }
        if self.config_entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_SCALE:
            settings[CONF_CONNECTION_DELAY] = (0, 30, DEFAULT_SCALE_CONNECTION_DELAY)
        return settings

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Validate integral limits before saving any new settings."""
        device_type = self.config_entry.data.get(
            CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE
        )
        is_scale = device_type == DEVICE_TYPE_SCALE
        has_users = device_type in USER_FILTER_DEVICE_TYPES
        errors = {}
        values = {}
        if user_input is not None:
            settings = self._settings()
            if has_users:
                settings[CONF_USER_ID] = (-1, 8 if is_scale else 254, -1)
            for key, (minimum, maximum, default) in settings.items():
                value = user_input.get(key, self.config_entry.options.get(key, default))
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not minimum <= value <= maximum
                    or int(value) != value
                ):
                    errors[key] = (
                        "invalid_scale_user_id"
                        if is_scale and key == CONF_USER_ID
                        else f"invalid_{key}"
                    )
                else:
                    values[key] = int(value)
            if has_users:
                values[CONF_SEPARATE_USERS] = user_input.get(CONF_SEPARATE_USERS, False)
            values[CONF_PAIR] = user_input.get(CONF_PAIR, False)
            if is_scale:
                epoch = user_input.get(CONF_SCALE_EPOCH)
                if epoch not in SCALE_EPOCHS:
                    errors[CONF_SCALE_EPOCH] = "invalid_scale_epoch"
                else:
                    values[CONF_SCALE_EPOCH] = epoch
            elif device_type == DEVICE_TYPE_BLOOD_PRESSURE:
                values[CONF_SYNC_TIME] = user_input.get(CONF_SYNC_TIME, False)
                values[CONF_CLOCK_BEFORE_SUBSCRIBE] = user_input.get(
                    CONF_CLOCK_BEFORE_SUBSCRIBE, False
                )
            if not errors:
                return self.async_create_entry(title="", data=values)
        fields = {}
        for key, (minimum, maximum, default) in self._settings().items():
            fields[
                vol.Required(key, default=self.config_entry.options.get(key, default))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        fields[
            vol.Required(
                CONF_PAIR, default=self.config_entry.options.get(CONF_PAIR, False)
            )
        ] = selector.BooleanSelector()
        if has_users:
            fields[
                vol.Required(
                    CONF_USER_ID,
                    default=self.config_entry.options.get(CONF_USER_ID, -1),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1,
                    max=8 if is_scale else 254,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            fields[
                vol.Required(
                    CONF_SEPARATE_USERS,
                    default=self.config_entry.options.get(CONF_SEPARATE_USERS, False),
                )
            ] = selector.BooleanSelector()
        if is_scale:
            fields[
                vol.Required(
                    CONF_SCALE_EPOCH,
                    default=self.config_entry.options.get(
                        CONF_SCALE_EPOCH,
                        self.config_entry.data.get(
                            CONF_SCALE_EPOCH,
                            default_scale_epoch(self.config_entry.title),
                        ),
                    ),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SCALE_EPOCHS), translation_key=CONF_SCALE_EPOCH
                )
            )
        elif device_type == DEVICE_TYPE_BLOOD_PRESSURE:
            for key in (CONF_SYNC_TIME, CONF_CLOCK_BEFORE_SUBSCRIBE):
                fields[
                    vol.Required(key, default=self.config_entry.options.get(key, False))
                ] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input
            ),
            errors=errors,
        )
