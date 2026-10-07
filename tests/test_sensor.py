"""Verify measurement lifetime, native units, timestamps and restore isolation."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import DATA_COMPONENT, SensorStateClass
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.medisana_ble.const import (
    CONF_DEVICE_TYPE,
    CONF_USER_ID,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
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
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Medisana test device",
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_DEVICE_TYPE: device_type,
        },
        options={CONF_USER_ID: user_id_filter},
    )
    entry.add_to_hass(hass)
    return MedisanaCoordinator(hass, entry)


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


@pytest.mark.parametrize(
    "key,device_type,value,expected_state_class",
    [
        ("systolic", DEVICE_TYPE_BLOOD_PRESSURE, "120.0", SensorStateClass.MEASUREMENT),
        ("diastolic", DEVICE_TYPE_BLOOD_PRESSURE, "80.0", SensorStateClass.MEASUREMENT),
        (
            "mean_arterial_pressure",
            DEVICE_TYPE_BLOOD_PRESSURE,
            "93.0",
            SensorStateClass.MEASUREMENT,
        ),
        ("pulse", DEVICE_TYPE_BLOOD_PRESSURE, "65.0", SensorStateClass.MEASUREMENT),
        ("temperature", DEVICE_TYPE_THERMOMETER, "36.5", SensorStateClass.MEASUREMENT),
        (
            "battery_level",
            DEVICE_TYPE_BLOOD_PRESSURE,
            "75",
            SensorStateClass.MEASUREMENT,
        ),
        ("battery_level", DEVICE_TYPE_THERMOMETER, "75", SensorStateClass.MEASUREMENT),
        ("user_id", DEVICE_TYPE_BLOOD_PRESSURE, "0", None),
        ("last_measurement", DEVICE_TYPE_BLOOD_PRESSURE, None, None),
        ("last_received", DEVICE_TYPE_BLOOD_PRESSURE, None, None),
        ("last_measurement", DEVICE_TYPE_THERMOMETER, None, None),
        ("last_received", DEVICE_TYPE_THERMOMETER, None, None),
    ],
)
async def test_statistics_state_class_in_home_assistant(
    hass, key, device_type, value, expected_state_class
):
    """Expose statistics metadata only for physical numeric measurements."""
    coordinator = make_coordinator(hass, device_type=device_type)
    sensor = await add_sensor(hass, coordinator, key)
    coordinator.battery_level = 75
    coordinator.received_at = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
    coordinator.async_set_updated_data(
        TemperatureMeasurement(
            temperature=36.5,
            unit="°C",
            timestamp=datetime(2026, 9, 16, 21, 15),
        )
        if device_type == DEVICE_TYPE_THERMOMETER
        else blood_pressure()
    )
    state = hass.states.get(sensor.entity_id)
    if value is not None:
        assert state.state == value
    if expected_state_class is None:
        assert "state_class" not in state.attributes
    else:
        assert state.attributes["state_class"] == expected_state_class


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


async def test_identical_temperature_measurements_publish_only_for_new_packets(hass):
    """Equal readings update measurement entities, but metadata refreshes do not."""
    coordinator = make_coordinator(hass, device_type=DEVICE_TYPE_THERMOMETER)
    temperature_sensor = await add_sensor(hass, coordinator, "temperature")
    measurement_sensor = await add_sensor(hass, coordinator, "last_measurement")
    received_sensor = await add_sensor(hass, coordinator, "last_received")
    events = {
        temperature_sensor.entity_id: [],
        measurement_sensor.entity_id: [],
        received_sensor.entity_id: [],
    }
    remove_listener = hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        lambda event: (
            events[event.data["entity_id"]].append(event)
            if event.data["entity_id"] in events
            else None
        ),
    )

    reading = TemperatureMeasurement(
        temperature=36.5,
        unit="°C",
        timestamp=datetime(2026, 9, 18, 8, 0),
    )
    for minute in (0, 1):
        coordinator.measurement_sequence += 1
        coordinator.received_at = datetime(2026, 9, 18, 8, minute, tzinfo=UTC)
        coordinator.async_set_updated_data(reading)
        await hass.async_block_till_done()

    # Device-information and battery refreshes use the same coordinator but
    # must not make an old temperature look like a newly received measurement.
    coordinator.async_update_listeners()
    remove_listener()

    assert [
        event.data["new_state"].state for event in events[temperature_sensor.entity_id]
    ] == [
        "36.5",
        "36.5",
    ]
    assert len(events[measurement_sensor.entity_id]) == 2
    assert [
        event.data["new_state"].state for event in events[received_sensor.entity_id]
    ] == ["2026-09-18T08:00:00+00:00", "2026-09-18T08:01:00+00:00"]


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


@pytest.mark.parametrize(
    "core_state,seeds_replay_detection",
    [(CoreState.starting, True), (CoreState.running, False)],
)
async def test_restore_seeds_blood_pressure_only_during_ha_startup(
    hass, core_state, seeds_replay_detection
):
    """A restart remembers the record, while a manual reload resets ordering."""
    await hass.config.async_set_time_zone("Europe/Zurich")
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "sensor.systolic",
                    "120.0",
                    {
                        "configured_user_id": -1,
                        "measurement_time": "2026-09-16T21:15:00+02:00",
                        "measurement_status": 0,
                        "pressure_unit": "mmHg",
                        "unit_of_measurement": "mmHg",
                        "user_id": 1,
                    },
                ),
                {"native_value": 120.0, "native_unit_of_measurement": "mmHg"},
            )
        ],
    )
    coordinator = make_coordinator(hass)
    previous_state = hass.state
    hass.set_state(core_state)
    try:
        await add_sensor(hass, coordinator, "systolic")
    finally:
        hass.set_state(previous_state)

    if seeds_replay_detection:
        assert coordinator.latest_timestamp == datetime(2026, 9, 16, 21, 15)
        assert coordinator._restored_values["systolic"] == 120.0
        assert coordinator._restored_values["user_id"] == 1
    else:
        assert coordinator.latest_timestamp is None
        assert not coordinator._restored_values


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
