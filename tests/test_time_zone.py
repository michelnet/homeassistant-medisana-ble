"""Keep time conversions on the event loop free of time-zone construction."""

from datetime import UTC, datetime, timedelta
from struct import pack
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import CoreState
from pytest_homeassistant.common import MockConfigEntry

from custom_components.medisana_ble import coordinator as coordinator_module
from custom_components.medisana_ble import history as history_module
from custom_components.medisana_ble import sensor as sensor_module
from custom_components.medisana_ble.const import CURRENT_TIME_UUID, DOMAIN
from custom_components.medisana_ble.coordinator import MedisanaCoordinator
from custom_components.medisana_ble.parser import BloodPressureMeasurement
from custom_components.medisana_ble.sensor import TIMESTAMP_SENSORS, MedisanaSensor


@pytest.fixture(params=[(1, 1), (7, 2)], ids=["winter", "summer"])
async def local_times(hass, request):
    """Use independently specified Swiss wall-clock times in both seasons."""
    await hass.config.async_set_time_zone("Europe/Zurich")
    month, offset = request.param
    utc = datetime(2026, month, 15, 12, 30, tzinfo=UTC)
    local = datetime(2026, month, 15, 12 + offset, 30)
    return utc, local, timedelta(hours=offset)


@pytest.fixture
def no_time_zone_lookup(monkeypatch, local_times):
    """Catch constructors even when ZoneInfo's cache would hide disk access."""
    forbidden = Mock(side_effect=AssertionError("Time zone loaded on the event loop"))
    # Also intercept imported aliases so this guards the original regression.
    for module in (coordinator_module, history_module, sensor_module):
        for name, value in vars(module).copy().items():
            if value is ZoneInfo:
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr("zoneinfo.ZoneInfo", forbidden)
    return forbidden


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Medisana",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        options={"separate_user_sensors": True, "history_limit": 0},
    )
    entry.add_to_hass(hass)
    return MedisanaCoordinator(hass, entry)


@pytest.mark.parametrize(
    "operation",
    ["history", "sensor", "restore_main", "restore_user", "load_history", "clock"],
)
async def test_time_paths_reuse_loaded_zone(
    hass, coordinator, local_times, no_time_zone_lookup, operation
):
    """All six reviewed paths use the loaded zone with correct seasonal offsets."""
    utc, local, offset = local_times
    measurement = BloodPressureMeasurement(
        systolic=120,
        diastolic=80,
        mean_arterial_pressure=93,
        pressure_unit="mmHg",
        pulse=65,
        user_id=1,
        timestamp=local,
        measurement_status=0,
    )
    if operation == "history":
        record = coordinator.history.record("blood_pressure", measurement, utc)
        timestamp = datetime.fromisoformat(record["timestamp"])
        assert timestamp == utc
        assert timestamp.utcoffset() == offset
        assert record["received_at"] == utc.isoformat()
    elif operation == "sensor":
        sensor = MedisanaSensor(coordinator, TIMESTAMP_SENSORS[0])
        sensor.hass = hass
        timestamp = sensor._device_timestamp(local)
        assert timestamp == utc
        assert timestamp.utcoffset() == offset
        assert sensor._device_timestamp(utc) is utc
        assert sensor._device_timestamp(None) is None
    elif operation == "restore_main":
        hass.set_state(CoreState.starting)
        coordinator.restore_sensor_data(
            "systolic", 120, "mmHg", {"measurement_time": utc.isoformat()}
        )
        assert coordinator.latest_timestamp == local
    elif operation == "restore_user":
        coordinator.restore_user_sensor_data(
            1, "systolic", 120, "mmHg", {"measurement_time": utc.isoformat()}
        )
        restored = coordinator._previous_user_measurement(1)
        assert restored.timestamp == local
        assert restored.systolic == 120
    elif operation == "load_history":
        coordinator.history.records = {
            "stored": {
                "systolic": 120,
                "diastolic": 80,
                "mean_arterial_pressure": 93,
                "pressure_unit": "mmHg",
                "pulse": 65,
                "user_id": 1,
                "measurement_status": 0,
                "timestamp": utc.isoformat(),
                "received_at": utc.isoformat(),
            }
        }
        with patch.object(coordinator.history, "async_load", new=AsyncMock()):
            await coordinator.async_initialize()
        assert coordinator.user_measurements[1] == measurement
        assert coordinator.user_received_at[1] == utc
    else:
        characteristic = MagicMock(properties=["write"])
        client = MagicMock()
        client.services.get_characteristic.return_value = characteristic
        client.write_gatt_char = AsyncMock()
        coordinator._client = client
        with patch.object(coordinator_module.dt_util, "utcnow", return_value=utc):
            await coordinator._async_sync_clock()
        client.services.get_characteristic.assert_called_once_with(CURRENT_TIME_UUID)
        client.write_gatt_char.assert_awaited_once_with(
            characteristic,
            pack(
                "<H8B",
                local.year,
                local.month,
                local.day,
                local.hour,
                local.minute,
                0,
                local.isoweekday(),
                0,
                2,
            ),
            response=True,
        )
    no_time_zone_lookup.assert_not_called()


async def test_existing_sensor_uses_changed_home_assistant_zone(hass, coordinator):
    """Changing the configured zone must not leave an integration-specific cache."""
    sensor = MedisanaSensor(coordinator, TIMESTAMP_SENSORS[0])
    sensor.hass = hass
    local = datetime(2026, 7, 15, 12, 30)
    for zone, utc_hour in (("Europe/Zurich", 10), ("America/New_York", 16)):
        await hass.config.async_set_time_zone(zone)
        assert sensor._device_timestamp(local).astimezone(UTC) == datetime(
            2026, 7, 15, utc_hour, 30, tzinfo=UTC
        )
