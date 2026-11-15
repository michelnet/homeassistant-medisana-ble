"""Medisana BLE measurements through Home Assistant's Bluetooth adapters."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import MedisanaCoordinator
from .history import MeasurementHistory
from .services import async_setup_services

PLATFORMS = [Platform.SENSOR]
type MedisanaConfigEntry = ConfigEntry[MedisanaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MedisanaConfigEntry) -> bool:
    """Set up a device even while it is asleep."""
    coordinator = MedisanaCoordinator(hass, entry)
    await coordinator.async_initialize()
    entry.runtime_data = coordinator
    async_setup_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: MedisanaConfigEntry
) -> None:
    """Reload after user, scale clock, timing or synchronization options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MedisanaConfigEntry) -> bool:
    """Unload entities and release all Bluetooth resources."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        if not any(
            other.entry_id != entry.entry_id
            and hasattr(other, "runtime_data")
            and not other.runtime_data._stopped
            for other in hass.config_entries.async_entries(DOMAIN)
        ):
            hass.services.async_remove(DOMAIN, "export_history")
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: MedisanaConfigEntry) -> None:
    """Delete this device's retained records when its entry is removed."""
    await MeasurementHistory(hass, entry.entry_id, 0).async_load()
