"""Administrator-only local CSV export of retained measurements."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register once; resolve the current coordinator on every invocation."""
    if hass.services.has_service(DOMAIN, "export_history"):
        return

    async def export_history(call: ServiceCall) -> dict:
        entry_id = call.data["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or not hasattr(entry, "runtime_data")
        ):
            raise ServiceValidationError(
                "Choose a loaded Medisana BLE configuration entry"
            )
        csv_data, count = entry.runtime_data.history.csv(call.data.get("user_id"))
        return {
            "csv": csv_data,
            "record_count": count,
            "filename": "medisana_measurements.csv",
        }

    async_register_admin_service(
        hass,
        DOMAIN,
        "export_history",
        export_history,
        schema=vol.Schema(
            {
                vol.Required("entry_id"): str,
                vol.Optional("user_id"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=254)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
