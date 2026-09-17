"""Medisana BLE measurements through Home Assistant's Bluetooth adapters."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import MedisanaCoordinator

PLATFORMS = [Platform.SENSOR]
type MedisanaConfigEntry = ConfigEntry[MedisanaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MedisanaConfigEntry) -> bool:
    """Set up a device even while it is asleep."""
    coordinator = MedisanaCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: MedisanaConfigEntry
) -> None:
    """Reload when the selected user changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MedisanaConfigEntry) -> bool:
    """Unload entities and release all Bluetooth resources."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        return True
    return False
