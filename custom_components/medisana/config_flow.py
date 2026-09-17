"""Discover and configure Medisana blood pressure monitors and thermometers."""

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
    CONF_DEVICE_TYPE,
    CONF_USER_ID,
    DEFAULT_BLOOD_PRESSURE_NAME,
    DEFAULT_NAME,
    DEFAULT_THERMOMETER_NAME,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_THERMOMETER,
    DISCOVERY_LOCAL_NAMES,
    DISCOVERY_MANUFACTURER_IDS,
    DOMAIN,
    HEALTH_THERMOMETER_SERVICE_UUID,
    THERMOMETER_LOCAL_NAMES,
)

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
    default_name = (
        DEFAULT_THERMOMETER_NAME
        if get_device_type(info) == DEVICE_TYPE_THERMOMETER
        else DEFAULT_BLOOD_PRESSURE_NAME
    )
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
        """Only blood pressure monitors have a user ID filter."""
        return (
            config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_BLOOD_PRESSURE)
            == DEVICE_TYPE_BLOOD_PRESSURE
        )

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
        """Use Home Assistant's standard one-click discovery confirmation."""
        if user_input is not None:
            self._abort_if_unique_id_configured()
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find nearby monitors, with an address fallback if they are asleep."""
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
            if not errors:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
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
                        options=[DEVICE_TYPE_BLOOD_PRESSURE, DEVICE_TYPE_THERMOMETER],
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
    """Filter blood pressure measurements by their transmitted user ID."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a specific user ID, or -1 for all users."""
        if not MedisanaConfigFlow.async_supports_options_flow(self.config_entry):
            return self.async_abort(reason="no_options_available")
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id = user_input[CONF_USER_ID]
            if (
                isinstance(user_id, bool)
                or not isinstance(user_id, (int, float))
                or not -1 <= user_id <= 254
                or int(user_id) != user_id
            ):
                errors[CONF_USER_ID] = "invalid_user_id"
            else:
                return self.async_create_entry(
                    title="", data={CONF_USER_ID: int(user_id)}
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USER_ID,
                        default=self.config_entry.options.get(CONF_USER_ID, -1),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=-1,
                            max=254,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
            errors=errors,
        )
