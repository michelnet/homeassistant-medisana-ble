"""Verify measurement lifetime, native units, timestamps and restore isolation."""

import logging
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.medisana_ble.const import (
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_THERMOMETER,
)
from custom_components.medisana_ble.coordinator import MedisanaCoordinator
from custom_components.medisana_ble.parser import (
    BloodPressureMeasurement,
)
from custom_components.medisana_ble.sensor import (
    BLOOD_PRESSURE_SENSORS,
    COMMON_SENSORS,
    THERMOMETER_SENSORS,
    TIMESTAMP_SENSORS,
    MedisanaSensor,
    async_setup_entry,
)
from custom_components.medisana_ble.thermometer import TemperatureMeasurement


def make_coordinator(
    hass: HomeAssistant,
    user_id_filter: int = -1,
    device_type: str = DEVICE_TYPE_BLOOD_PRESSURE,
) -> MedisanaCoordinator:
    """Use real coordinator listener behavior without connecting to Bluetooth."""
    coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        name="Medisana test device",
        config_entry=None,
    )
    coordinator.address = "AA:BB:CC:DD:EE:FF"
    coordinator.name = "Medisana test device"
    coordinator.user_id_filter = user_id_filter
    coordinator.device_type = device_type
    coordinator.device_info = {}
    coordinator.battery_level = None
    coordinator.received_at = None
    return cast(MedisanaCoordinator, coordinator)


async def add_sensor(
    hass: HomeAssistant,
    coordinator: MedisanaCoordinator,
    key: str,
) -> MedisanaSensor:
    """Add a sensor through Home Assistant's actual sensor entity platform."""
    description = next(
        item
        for item in (
            *BLOOD_PRESSURE_SENSORS,
            *THERMOMETER_SENSORS,
            *COMMON_SENSORS,
            *TIMESTAMP_SENSORS,
        )
        if item.key == key
    )
    entity = MedisanaSensor(coordinator, description)
    entity.entity_id = f"sensor.{key}"
    assert await async_setup_component(hass, "sensor", {})
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    return entity


def blood_pressure(**changes) -> BloodPressureMeasurement:
    """Build a complete reading while allowing optional values to vary."""
    return BloodPressureMeasurement(
        **{
            "systolic": 120.0,
            "diastolic": 80.0,
            "mean_arterial_pressure": 93.0,
            "pressure_unit": "mmHg",
            "pulse": 65.0,
            "user_id": 0,
            "timestamp": datetime(2026, 9, 16, 21, 15),
            "measurement_status": 0,
            **changes,
        }
    )


async def test_sleeping_device_preserves_accepted_measurement(hass):
    """The first reading is unknown; disconnecting must not erase a reading."""
    coordinator = make_coordinator(hass)
    sensor = await add_sensor(hass, coordinator, "systolic")
    assert hass.states.get(sensor.entity_id).state == "unknown"

    coordinator.async_set_updated_data(blood_pressure())
    assert hass.states.get(sensor.entity_id).state == "120.0"
    assert hass.states.get(sensor.entity_id).attributes["user_id"] == 0

    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    assert hass.states.get(sensor.entity_id).state == "120.0"
    assert sensor.available


async def test_missing_optional_value_does_not_mix_readings(hass):
    """A new record lacking pulse/user fields must not inherit the prior ones."""
    coordinator = make_coordinator(hass)
    sensor = await add_sensor(hass, coordinator, "pulse")
    coordinator.async_set_updated_data(blood_pressure())
    assert hass.states.get(sensor.entity_id).state == "65.0"
    coordinator.async_set_updated_data(blood_pressure(pulse=None, user_id=None))
    assert hass.states.get(sensor.entity_id).state == "unknown"
    assert "user_id" not in hass.states.get(sensor.entity_id).attributes


async def test_pressure_keeps_native_kpa_and_displays_mmhg(hass):
    """A kPa payload must never be mislabeled as mmHg without conversion."""
    coordinator = make_coordinator(hass)
    sensor = await add_sensor(hass, coordinator, "systolic")
    coordinator.async_set_updated_data(
        blood_pressure(systolic=16.0, pressure_unit="kPa")
    )
    assert sensor.native_unit_of_measurement == "kPa"
    assert sensor.native_value == 16.0
    state = hass.states.get(sensor.entity_id)
    assert state.attributes["unit_of_measurement"] == "mmHg"
    assert float(state.state) == pytest.approx(120.01, abs=0.05)


async def test_device_time_and_reception_time_are_distinct(hass):
    """Stored device time uses the configured zone, not the reception time."""
    await hass.config.async_set_time_zone("Europe/Zurich")
    coordinator = make_coordinator(hass)
    measurement_sensor = await add_sensor(hass, coordinator, "last_measurement")
    received_sensor = await add_sensor(hass, coordinator, "last_received")
    coordinator.received_at = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
    coordinator.async_set_updated_data(blood_pressure())
    assert hass.states.get(measurement_sensor.entity_id).state == (
        "2026-09-16T19:15:00+00:00"
    )
    assert hass.states.get(received_sensor.entity_id).state == (
        "2026-09-16T20:00:00+00:00"
    )
    coordinator.async_set_updated_data(blood_pressure(timestamp=None))
    assert hass.states.get(measurement_sensor.entity_id).state == "unknown"


async def test_identical_temperature_readings_publish_separate_updates(hass):
    """The thermometer can send a new reading with the same numeric value."""
    coordinator = make_coordinator(hass, device_type=DEVICE_TYPE_THERMOMETER)
    sensor = await add_sensor(hass, coordinator, "temperature")
    events = []
    remove_listener = hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        lambda event: (
            events.append(event)
            if event.data["entity_id"] == sensor.entity_id
            else None
        ),
    )

    reading = TemperatureMeasurement(temperature=36.5, unit="°C")
    coordinator.async_set_updated_data(reading)
    coordinator.async_set_updated_data(reading)
    remove_listener()

    assert [event.data["new_state"].state for event in events] == ["36.5", "36.5"]


@pytest.mark.parametrize(
    "stored_filter,current_filter,restores",
    [
        (-1, -1, True),
        (0, 0, True),
        (-1, 0, False),
        (0, 1, False),
        (1, -1, False),
    ],
)
async def test_restore_preserves_units_and_separates_user_filters(
    hass, stored_filter, current_filter, restores
):
    """Restore the native unit, and never show data from another configured user."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "sensor.systolic",
                    "120.0",
                    {
                        "configured_user_id": stored_filter,
                        "user_id": 0,
                        "unit_of_measurement": "mmHg",
                    },
                ),
                {"native_value": 16.0, "native_unit_of_measurement": "kPa"},
            )
        ],
    )
    coordinator = make_coordinator(hass, user_id_filter=current_filter)
    sensor = await add_sensor(hass, coordinator, "systolic")
    if restores:
        assert sensor.native_value == 16.0
        assert sensor.native_unit_of_measurement == "kPa"
        assert hass.states.get(sensor.entity_id).attributes["user_id"] == 0
    else:
        assert sensor.native_value is None
        assert hass.states.get(sensor.entity_id).state == "unknown"


async def test_current_reading_takes_priority_over_restored_state(hass):
    """Do not replace a notification received during startup with old storage."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("sensor.systolic", "180", {"configured_user_id": -1}),
                {"native_value": 180.0, "native_unit_of_measurement": "mmHg"},
            )
        ],
    )
    coordinator = make_coordinator(hass)
    coordinator.data = blood_pressure()
    sensor = await add_sensor(hass, coordinator, "systolic")
    assert sensor.native_value == 120.0


async def test_platform_exposes_blood_pressure_sensors(hass):
    coordinator = make_coordinator(hass)
    entry = Mock(runtime_data=coordinator)
    add_entities = Mock()
    await async_setup_entry(hass, entry, add_entities)
    assert {
        entity.entity_description.key for entity in add_entities.call_args.args[0]
    } == {
        "systolic",
        "diastolic",
        "mean_arterial_pressure",
        "pulse",
        "user_id",
        "battery_level",
        "last_measurement",
        "last_received",
    }
