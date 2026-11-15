"""User isolation, private history and connection tuning using synthetic frames."""

from csv import DictReader
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from struct import pack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.medisana_ble.const import (
    BLOOD_PRESSURE_CHARACTERISTIC_UUID,
    CURRENT_TIME_UUID,
    DEVICE_TYPE_SCALE,
    DOMAIN,
    EVENT_MEASUREMENT,
    SCALE_BODY_UUID,
    SCALE_USER_UUID,
    SCALE_WEIGHT_UUID,
)
from custom_components.medisana_ble.coordinator import MedisanaCoordinator
from custom_components.medisana_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.medisana_ble.history import MeasurementHistory
from custom_components.medisana_ble.parser import parse_blood_pressure
from custom_components.medisana_ble.scale import ScaleDecoder, ScaleMeasurement

MODULE = "custom_components.medisana_ble.coordinator"
ADDRESS = "AA:BB:CC:DD:EE:FF"
NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("mock_bluetooth")


def pressure(user, hour=10, systolic=120):
    return pack("<BHHH", 0x0E, systolic, 80, 93) + pack(
        "<HBBBBBHB", 2026, 11, 15, hour, 0, 0, 65, user
    )


def weight(user=2, seconds=532000000, value=7820):
    return pack("<BHxxIxxxxB", 0x1D, value, seconds, user)


def body(user=2, seconds=532000000):
    return pack("<BIBHHHHH", 0x6F, seconds, user, 1700, 184, 600, 450, 30)


@pytest.fixture
def no_radio():
    with (
        patch(f"{MODULE}.bluetooth.async_register_callback", return_value=MagicMock()),
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=False),
    ):
        yield


def entry_for(hass, device_type="blood_pressure", options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Medisana",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "device_type": device_type},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def entities(hass, entry):
    return {
        item.unique_id.removeprefix(f"{ADDRESS.lower()}_"): item.entity_id
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }


@pytest.mark.parametrize("body_first", [False, True])
def test_bs550_guest_weight_is_unassigned_and_composition_ignored(body_first):
    decoder = ScaleDecoder("2010")
    decoder.feed(SCALE_USER_UUID, bytes.fromhex("84 00 02 00 01 32 b4 00 00"))
    decoder.feed(SCALE_WEIGHT_UUID, weight())
    if body_first:
        assert decoder.feed(SCALE_BODY_UUID, body(255)) is None
    result = decoder.feed(SCALE_WEIGHT_UUID, weight(255))
    assert result.weight == 78.2
    assert result.user_id is None and result.raw_user_id == 255
    assert result.bmi is None and result.body_fat is None
    assert decoder.feed(SCALE_BODY_UUID, body(255)) is None
    known = decoder.feed(SCALE_BODY_UUID, body())
    assert known.user_id == 2 and known.body_fat == 18.4


async def test_history_retention_deduplication_export_and_disabled_purge(hass):
    history = MeasurementHistory(hass, "private", 2)
    await history.async_load()
    readings = [parse_blood_pressure(pressure(1, hour)) for hour in (12, 10, 11)]
    for reading in readings:
        history.record("blood_pressure", reading, NOW)
    assert len(history.records) == 2
    assert {
        datetime.fromisoformat(r["timestamp"]).hour for r in history.records.values()
    } == {11, 12}
    assert (
        history.record("blood_pressure", readings[0], NOW + timedelta(seconds=1))
        is None
    )
    await history.async_flush()
    reloaded = MeasurementHistory(hass, "private", 2)
    await reloaded.async_load()
    assert reloaded.records == history.records
    csv_data, count = reloaded.csv(1)
    exported = list(DictReader(StringIO(csv_data)))
    assert count == len(exported) == 2
    assert exported[0]["systolic"] == "120.0"
    assert exported[0]["timestamp"] and exported[0]["received_at"]
    assert reloaded.csv(0)[1] == 0
    disabled = MeasurementHistory(hass, "private", 0)
    await disabled.async_load()
    assert await disabled._store.async_load() is None
    disabled.record("blood_pressure", readings[0], NOW)
    assert disabled.csv()[1] == 0


async def test_scale_history_updates_one_record_and_preserves_composition(hass):
    history = MeasurementHistory(hass, "scale", 200)
    reading = ScaleMeasurement(78.2, 2, NOW, raw_user_id=2)
    first = history.record("scale", reading, NOW)
    assert not first["updated"]
    enriched = replace(reading, body_fat=18.4)
    second = history.record("scale", enriched, NOW + timedelta(seconds=1))
    assert second["updated"] and second["id"] == first["id"]
    assert len(history.records) == 1
    assert history.record("scale", reading, NOW + timedelta(seconds=2)) is None
    assert list(history.records.values())[0]["body_fat"] == 18.4


async def test_history_and_events_keep_old_and_filtered_records(hass):
    entry = entry_for(hass, options={"user_id": 1})
    coordinator = MedisanaCoordinator(hass, entry)
    events = []
    hass.bus.async_listen(EVENT_MEASUREMENT, events.append)
    coordinator._async_handle_packet(pressure(1, 12))
    coordinator._async_handle_packet(pressure(1, 10))
    coordinator._async_handle_packet(pressure(2, 11))
    coordinator._async_handle_packet(pressure(2, 11))
    await hass.async_block_till_done()
    assert len(coordinator.history.records) == len(events) == 3
    assert coordinator.data.user_id == 1 and coordinator.data.timestamp.hour == 12
    assert coordinator.measurement_sequence == 1
    assert coordinator.history.csv(2)[1] == 1
    await coordinator.async_stop()


async def test_user_groups_are_isolated_persisted_and_leave_main_ids_unchanged(
    hass, no_radio
):
    entry = entry_for(hass, options={"user_id": 1, "separate_user_sensors": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    coordinator._async_handle_packet(pressure(1, 12, 123))
    coordinator._async_handle_packet(pressure(0, 10, 110))
    coordinator._async_handle_packet(pressure(1, 9, 100))
    coordinator._async_handle_packet(pressure(255, 11, 130))
    await hass.async_block_till_done()
    ids = entities(hass, entry)
    assert hass.states.get(ids["systolic"]).state == "123.0"
    assert hass.states.get(ids["user_0_systolic"]).state == "110.0"
    assert hass.states.get(ids["user_1_systolic"]).state == "123.0"
    assert "user_255_systolic" not in ids
    assert len(coordinator.user_measurements) == 2
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entities(hass, entry) == ids
    assert hass.states.get(ids["user_0_systolic"]).state == "110.0"
    assert hass.states.get(ids["user_1_systolic"]).state == "123.0"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_scale_guest_does_not_create_user_group_or_overwrite_known_user(
    hass, no_radio
):
    entry = entry_for(hass, DEVICE_TYPE_SCALE, {"separate_user_sensors": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    coordinator._async_handle_packet(weight(), SCALE_WEIGHT_UUID)
    coordinator._async_handle_packet(body(), SCALE_BODY_UUID)
    coordinator._async_handle_packet(weight(255, 532000001, 8000), SCALE_WEIGHT_UUID)
    await hass.async_block_till_done()
    ids = entities(hass, entry)
    assert float(hass.states.get(ids["weight"]).state) == 80
    assert hass.states.get(ids["user_id"]).state == "unknown"
    assert hass.states.get(ids["weight"]).attributes["raw_user_id"] == 255
    assert float(hass.states.get(ids["user_2_weight"]).state) == 78.2
    assert float(hass.states.get(ids["user_2_body_fat"]).state) == 18.4
    assert "user_255_weight" not in ids
    assert coordinator.history.csv()[1] == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_export_requires_admin_and_rejects_unloaded_entry(hass, no_radio):
    entry = entry_for(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data._async_handle_packet(pressure(1))
    admin = await hass.auth.async_create_user("Admin", group_ids=["system-admin"])
    user = await hass.auth.async_create_user("User", group_ids=["system-users"])
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "export_history",
            {"entry_id": entry.entry_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )
    response = await hass.services.async_call(
        DOMAIN,
        "export_history",
        {"entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
        context=Context(user_id=admin.id),
    )
    from homeassistant.helpers.service import async_get_all_descriptions

    descriptions = await async_get_all_descriptions(hass)
    assert descriptions[DOMAIN]["export_history"]["fields"]["entry_id"]["required"]
    assert response["record_count"] == 1
    assert list(DictReader(StringIO(response["csv"])))[0]["user_id"] == "1"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "export_history",
            {"entry_id": "missing"},
            blocking=True,
            return_response=True,
            context=Context(user_id=admin.id),
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "export_history")


async def test_diagnostics_omit_address_serial_and_health_data(hass):
    entry = entry_for(hass)
    entry.runtime_data = coordinator = MedisanaCoordinator(hass, entry)
    coordinator.device_info = {
        "model_number": "BU572",
        "firmware_revision": "1.2",
        "serial_number": "SECRET",
    }
    coordinator._async_handle_packet(pressure(1, 12, 121))
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["device_information"]["firmware_revision"] == "1.2"
    assert ADDRESS not in str(result) and "SECRET" not in str(result)
    assert "systolic" not in str(result) and "user_id" not in str(result)
    assert result["connection"]["malformed_packets"] == 0
    coordinator._async_handle_packet(b"bad")
    assert coordinator.connection_diagnostics["malformed_packets"] == 1
    await coordinator.async_stop()


async def test_bu572_clock_before_subscribe_and_pairing_timeout_options(hass):
    entry = entry_for(
        hass,
        options={
            "pair": True,
            "sync_time": True,
            "clock_before_subscribe": True,
            "connection_timeout": 3,
            "connection_attempts": 10,
        },
    )
    coordinator = MedisanaCoordinator(hass, entry)
    chars = {
        BLOOD_PRESSURE_CHARACTERISTIC_UUID: MagicMock(
            uuid=BLOOD_PRESSURE_CHARACTERISTIC_UUID, properties=["indicate"]
        ),
        CURRENT_TIME_UUID: MagicMock(uuid=CURRENT_TIME_UUID, properties=["write"]),
    }
    client = MagicMock(is_connected=True)
    client.services.get_characteristic.side_effect = chars.get
    client.disconnect = AsyncMock()
    calls = []
    client.write_gatt_char = AsyncMock(
        side_effect=lambda *args, **kwargs: calls.append("clock")
    )
    client.start_notify = AsyncMock(side_effect=lambda *args: calls.append("subscribe"))
    coordinator._async_read_optional_device_info = AsyncMock()
    connector = AsyncMock(return_value=client)
    # End the bounded session after successful subscription.
    original_subscribe = coordinator._async_subscribe

    async def subscribe():
        assert await original_subscribe()
        raise TimeoutError

    coordinator._async_subscribe = subscribe
    with (
        patch(f"{MODULE}.establish_connection", connector),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
    ):
        await coordinator._async_listen()
    assert calls == ["clock", "subscribe"]
    assert connector.call_args.kwargs["pair"] is True
    assert connector.call_args.kwargs["timeout"] == 3
    assert connector.call_args.kwargs["max_attempts"] == 10
    client.disconnect.assert_awaited_once()
    await coordinator.async_stop()


async def test_actual_connect_attempt_respects_custom_timeout(hass):
    """Retry-connector's fixed connect timeout must not override this setting."""
    import asyncio

    from custom_components.medisana_ble.coordinator import MedisanaBleakClient

    client = MedisanaBleakClient(ADDRESS, timeout=0.01)

    async def slow_connect(self, **kwargs):
        assert kwargs["timeout"] == 0.01
        await asyncio.sleep(1)

    with patch(
        "bleak_retry_connector.BleakClientWithServiceCache.connect", slow_connect
    ):
        with pytest.raises(TimeoutError):
            await client.connect(timeout=20)


@pytest.mark.parametrize("history_limit", [0, 1])
async def test_user_group_restores_without_retained_record(
    hass, no_radio, history_limit
):
    entry = entry_for(
        hass, options={"separate_user_sensors": True, "history_limit": history_limit}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data._async_handle_packet(pressure(0, 10, 110))
    entry.runtime_data._async_handle_packet(pressure(1, 11, 120))
    await hass.async_block_till_done()
    ids = entities(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entities(hass, entry) == ids
    assert hass.states.get(ids["user_0_systolic"]).state == "110.0"
    assert hass.states.get(ids["user_1_systolic"]).state == "120.0"
    # A memory replay after reload must not replace per-user restored values.
    entry.runtime_data._async_handle_packet(pressure(0, 9, 100))
    entry.runtime_data._async_handle_packet(pressure(1, 9, 100))
    await hass.async_block_till_done()
    assert hass.states.get(ids["user_0_systolic"]).state == "110.0"
    assert hass.states.get(ids["user_1_systolic"]).state == "120.0"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_equal_temperature_packets_at_same_reception_time_remain_separate(hass):
    from custom_components.medisana_ble.thermometer import parse_temperature_measurement

    history = MeasurementHistory(hass, "thermometer", 200)
    reading = parse_temperature_measurement(b"\x00" + pack("<I", 0xFF00016D))
    first = history.record("thermometer", reading, NOW)
    second = history.record("thermometer", reading, NOW)
    assert first["id"] != second["id"]
    assert len(history.records) == 2


async def test_scale_identity_includes_user_even_without_optional_raw_id(hass):
    history = MeasurementHistory(hass, "scale", 200)
    history.record("scale", ScaleMeasurement(78.2, 1, NOW), NOW)
    history.record("scale", ScaleMeasurement(78.2, 2, NOW), NOW)
    assert len(history.records) == 2
    assert history.csv(1)[1] == history.csv(2)[1] == 1


async def test_removing_entry_deletes_its_private_history(hass, no_radio):
    entry = entry_for(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data
    coordinator._async_handle_packet(pressure(1))
    await coordinator.history.async_flush()
    assert await coordinator.history._store.async_load() is not None
    assert (await hass.config_entries.async_remove(entry.entry_id))[
        "require_restart"
    ] is False
    assert (
        await MeasurementHistory(hass, entry.entry_id, 200)._store.async_load() is None
    )


async def test_scale_user_restore_preserves_composition_on_weight_replay(
    hass, no_radio
):
    entry = entry_for(
        hass, DEVICE_TYPE_SCALE, {"separate_user_sensors": True, "history_limit": 0}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data._async_handle_packet(weight(), SCALE_WEIGHT_UUID)
    entry.runtime_data._async_handle_packet(body(), SCALE_BODY_UUID)
    await hass.async_block_till_done()
    ids = entities(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data._async_handle_packet(weight(), SCALE_WEIGHT_UUID)
    await hass.async_block_till_done()
    assert float(hass.states.get(ids["user_2_body_fat"]).state) == 18.4
    entry.runtime_data._async_handle_packet(
        weight(seconds=532000001), SCALE_WEIGHT_UUID
    )
    await hass.async_block_till_done()
    assert hass.states.get(ids["user_2_body_fat"]).state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unsupported_pairing_cleans_up_partial_connection(hass):
    from custom_components.medisana_ble.coordinator import MedisanaBleakClient

    client = MedisanaBleakClient(ADDRESS, pair=True)
    with (
        patch(
            "bleak_retry_connector.BleakClientWithServiceCache.connect",
            AsyncMock(side_effect=NotImplementedError),
        ),
        patch.object(client, "disconnect", AsyncMock()) as disconnect,
    ):
        with pytest.raises(NotImplementedError):
            await client.connect()
        disconnect.assert_awaited_once()
