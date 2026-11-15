"""Connection diagnostics without measurement values or device identifiers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Use Home Assistant's authenticated diagnostic download."""
    coordinator = entry.runtime_data
    return {
        "device_type": coordinator.device_type,
        "oximeter_protocol": coordinator.oximeter_protocol,
        "device_information": {
            key: value
            for key, value in coordinator.device_info.items()
            if key
            in {
                "manufacturer_name",
                "model_number",
                "firmware_revision",
                "hardware_revision",
            }
        },
        "connection": coordinator.connection_diagnostics.copy(),
        "gatt_endpoints": [
            {
                **endpoint,
                "service": (
                    "ba11f08c-5f14-0b0d-**REDACTED**"
                    if endpoint["service"].lower().startswith("ba11f08c-5f14-0b0d-")
                    else endpoint["service"]
                ),
            }
            for endpoint in coordinator.gatt_endpoints
        ],
        "settings": {
            "connection_timeout": coordinator.connection_timeout,
            "connection_attempts": coordinator.connection_attempts,
            "pair": coordinator.pair,
            "sync_time": coordinator.sync_time,
            "clock_before_subscribe": coordinator.clock_before_subscribe,
            "scale_epoch": coordinator.scale_epoch,
            "connection_delay": coordinator.scale_connection_delay,
            "separate_user_sensors": coordinator.separate_users,
            "history_limit": coordinator.history.limit,
        },
    }
