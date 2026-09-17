"""Exercise entry setup, reload and teardown through Home Assistant."""

from collections.abc import Generator
from struct import pack
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medisana_ble.const import (
    CONF_DEVICE_TYPE,
    CONF_USER_ID,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("mock_bluetooth")
ADDRESS = "AA:BB:CC:DD:EE:FF"
MODULE = "custom_components.medisana_ble.coordinator"


@pytest.fixture
def radio_hooks() -> Generator[tuple[MagicMock, MagicMock]]:
    """Keep real entry/coordinator lifecycles, with the device out of radio range."""
    unsubscribe = MagicMock()
    with (
        patch(
            f"{MODULE}.bluetooth.async_register_callback", return_value=unsubscribe
        ) as register,
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=False),
        patch(
            f"{MODULE}.establish_connection",
            side_effect=AssertionError("A sleeping device must not be connected"),
        ),
    ):
        yield register, unsubscribe


@pytest.fixture
def test_unsubscribe() -> MagicMock:
    """Provide an unsubscribe mock for cleanup tests."""
    return MagicMock()


def make_entry(device_type: str = DEVICE_TYPE_BLOOD_PRESSURE) -> MockConfigEntry:
    """Create a device configuration as produced by the config flow."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Medisana BU-570",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_DEVICE_TYPE: device_type},
     )


def entry_entities(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Look up entity IDs without depending on the UI language."""
    return {
        entity.unique_id.removeprefix(f"{ADDRESS.lower()}_"): entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }


async def test_setup_and_unload_sleeping_device(
    hass: HomeAssistant, radio_hooks
) -> None:
    """Setup exposes unknown sensors immediately and unload releases listeners."""
    register, unsubscribe = radio_hooks
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.data is None
    assert coordinator._task is None
    assert not coordinator._stopped
    register.assert_called_once()
    entities = entry_entities(hass, entry)
    assert set(entities) == {
        "systolic",
        "diastolic",
        "mean_arterial_pressure",
        "pulse",
        "user_id",
        "battery_level",
        "last_measurement",
        "last_received",
    }
    assert all(
        hass.states.get(entity_id).state == "unknown" for entity_id in entities.values()
    )
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].manufacturer == "Medisana"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator._stopped
    assert not coordinator._unsubscribers
    test_unsubscribe.assert_called_once()
    assert all(
        hass.data[DATA_COMPONENT].get_entity(entity_id) is None
        for entity_id in entities.values()
    )
    assert all(
        hass.states.get(entity_id).state == "unavailable"
        for entity_id in entities.values()
    )


async def test_options_reload_replaces_runtime_without_cross_user_restore(
    hass: HomeAssistant, radio_hooks
) -> None:
    """Changing the user filter reloads the entry and clears the prior user's data."""
    register, unsubscribe = radio_hooks
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    old_coordinator = entry.runtime_data
    entities = entry_entities(hass, entry)
    old_coordinator._async_handle_packet(pack("<BHHHHB", 0x0C, 120, 80, 93, 65, 1))
    assert float(hass.states.get(entities["systolic"]).state) == 120

    hass.config_entries.async_update_entry(entry, options={CONF_USER_ID: 0})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not old_coordinator
    assert old_coordinator._stopped
    assert entry.runtime_data.user_id_filter == 0
    assert entry_entities(hass, entry) == entities
    assert hass.states.get(entities["systolic"]).state == "unknown"
    assert register.call_count == 2
    test_unsubscribe.assert_called_once()

    entry.runtime_data._async_handle_packet(pack("<BHHHHB", 0x0C, 118, 78, 91, 64, 0))
    assert float(hass.states.get(entities["systolic"]).state) == 118
    assert hass.states.get(entities["systolic"]).attributes["user_id"] == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


