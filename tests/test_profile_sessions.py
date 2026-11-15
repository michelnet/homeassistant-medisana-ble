"""Exercise new GATT sessions through real Home Assistant sensor entities."""

import asyncio
from struct import pack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.medisana_ble.const import (
    BLOOD_PRESSURE_CHARACTERISTIC_UUID,
    CONF_CONNECTION_DELAY,
    CONF_DEVICE_TYPE,
    CONF_SCALE_EPOCH,
    CONF_SYNC_TIME,
    CONF_USER_ID,
    CURRENT_TIME_UUID,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_GLUCOSE,
    DEVICE_TYPE_PULSE_OXIMETER,
    DEVICE_TYPE_SCALE,
    DOMAIN,
    GLUCOSE_CONTEXT_UUID,
    GLUCOSE_MEASUREMENT_UUID,
    PLX_CONTINUOUS_UUID,
    PLX_SPOT_CHECK_UUID,
    RECORD_ACCESS_CONTROL_UUID,
    SCALE_BODY_UUID,
    SCALE_COMMAND_UUID,
    SCALE_USER_UUID,
    SCALE_WEIGHT_UUID,
)
from custom_components.medisana_ble.coordinator import MedisanaCoordinator

pytestmark = pytest.mark.usefixtures("mock_bluetooth")
MODULE = "custom_components.medisana_ble.coordinator"
ADDRESS = "AA:BB:CC:DD:EE:FF"
DATE = pack("<HBBBBB", 2026, 11, 15, 8, 30, 0)
GLUCOSE = b"\x12\x01\x00" + DATE + pack("<HB", 0xB064, 0x11)
WEIGHT = pack("<BHxxIxxxxB", 0x1D, 7820, 532000000, 2)
BODY = pack("<BIBHHHHH", 0x6F, 532000000, 2, 1700, 0xF0B8, 0xF258, 0xF1C2, 0xF01E)


def entry_for(hass, device_type, *, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Medisana BS 444 connect"
        if device_type == DEVICE_TYPE_SCALE
        else "Medisana",
        unique_id=ADDRESS,
        data={"address": ADDRESS, CONF_DEVICE_TYPE: device_type},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def client_for(uuids):
    characteristics = {
        uuid: MagicMock(uuid=uuid, properties=properties)
        for uuid, properties in uuids.items()
    }
    client = MagicMock(is_connected=True)
    client.services.get_characteristic.side_effect = characteristics.get
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.disconnect = AsyncMock()
    return client, characteristics


@pytest.mark.parametrize(
    "device_type", [DEVICE_TYPE_SCALE, DEVICE_TYPE_PULSE_OXIMETER, DEVICE_TYPE_GLUCOSE]
)
async def test_new_profiles_publish_real_entities_and_keep_values_while_asleep(
    hass, device_type
):
    entry = entry_for(
        hass,
        device_type,
        options={CONF_CONNECTION_DELAY: 0} if device_type == DEVICE_TYPE_SCALE else {},
    )
    uuids = {
        DEVICE_TYPE_SCALE: {
            SCALE_WEIGHT_UUID: ["indicate"],
            SCALE_BODY_UUID: ["indicate"],
            SCALE_USER_UUID: ["indicate"],
            SCALE_COMMAND_UUID: ["write"],
        },
        DEVICE_TYPE_PULSE_OXIMETER: {
            PLX_SPOT_CHECK_UUID: ["indicate"],
            PLX_CONTINUOUS_UUID: ["notify"],
        },
        DEVICE_TYPE_GLUCOSE: {
            GLUCOSE_MEASUREMENT_UUID: ["notify"],
            GLUCOSE_CONTEXT_UUID: ["notify"],
            RECORD_ACCESS_CONTROL_UUID: ["write", "indicate"],
        },
    }[device_type]
    client, chars = client_for(uuids)
    ready = asyncio.Event()
    callbacks = {}
    disconnected_callback = None

    async def subscribe(characteristic, callback):
        callbacks[characteristic.uuid] = callback
        if device_type == DEVICE_TYPE_PULSE_OXIMETER and len(callbacks) == 2:
            ready.set()

    async def write(characteristic, value, *, response):
        if device_type == DEVICE_TYPE_SCALE:
            assert set(callbacks) == {
                SCALE_WEIGHT_UUID,
                SCALE_BODY_UUID,
                SCALE_USER_UUID,
            }
            assert characteristic.uuid == SCALE_COMMAND_UUID and value[0] == 2
        else:
            assert set(callbacks) == {
                GLUCOSE_MEASUREMENT_UUID,
                GLUCOSE_CONTEXT_UUID,
                RECORD_ACCESS_CONTROL_UUID,
            }
            assert (
                characteristic.uuid == RECORD_ACCESS_CONTROL_UUID
                and value == b"\x01\x01"
            )
        assert response
        ready.set()

    async def connect(*args, **kwargs):
        nonlocal disconnected_callback
        disconnected_callback = kwargs["disconnected_callback"]
        return client

    client.start_notify.side_effect = subscribe
    client.write_gatt_char.side_effect = write
    with (
        patch(f"{MODULE}.bluetooth.async_register_callback", return_value=MagicMock()),
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await asyncio.wait_for(ready.wait(), 3)
        coordinator = entry.runtime_data
        assert entry.state is ConfigEntryState.LOADED
        if device_type == DEVICE_TYPE_SCALE:
            callbacks[SCALE_USER_UUID](
                chars[SCALE_USER_UUID], bytearray.fromhex("84 00 02 00 01 32 b4 00 00")
            )
            callbacks[SCALE_WEIGHT_UUID](chars[SCALE_WEIGHT_UUID], bytearray(WEIGHT))
            callbacks[SCALE_BODY_UUID](chars[SCALE_BODY_UUID], bytearray(BODY))
            expected = {
                "weight": (78.2, "kg"),
                "body_fat": (18.4, "%"),
                "bmi": (24.1, "kg/m²"),
                "bone_mass": (3, "kg"),
            }
        elif device_type == DEVICE_TYPE_PULSE_OXIMETER:
            callbacks[PLX_SPOT_CHECK_UUID](
                chars[PLX_SPOT_CHECK_UUID], bytearray(b"\x00" + pack("<HH", 97, 65))
            )
            expected = {"oxygen_saturation": (97, "%"), "pulse": (65, "bpm")}
        else:
            callbacks[GLUCOSE_MEASUREMENT_UUID](
                chars[GLUCOSE_MEASUREMENT_UUID], bytearray(GLUCOSE)
            )
            await hass.async_block_till_done()
            assert coordinator.data is None
            callbacks[GLUCOSE_CONTEXT_UUID](
                chars[GLUCOSE_CONTEXT_UUID], bytearray.fromhex("02 01 00 02")
            )
            expected = {"glucose": (100, "mg/dL")}
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        for key, (value, unit) in expected.items():
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{ADDRESS.lower()}_{key}"
            )
            state = hass.states.get(entity_id)
            assert float(state.state) == value
            assert state.attributes["unit_of_measurement"] == unit
            assert state.attributes["state_class"] == "measurement"
            if device_type == DEVICE_TYPE_GLUCOSE:
                assert (
                    state.attributes["meal"] == 2
                    and state.attributes["sequence_number"] == 1
                )
                assert "configured_user_id" not in state.attributes
        task = coordinator._task
        disconnected_callback(client)
        await asyncio.wait_for(task, 1)
        assert float(hass.states.get(entity_id).state) == value
        client.disconnect.assert_awaited_once()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_scale_filter_ordering_and_new_reading_clears_old_composition(hass):
    coordinator = MedisanaCoordinator(
        hass, entry_for(hass, DEVICE_TYPE_SCALE, options={CONF_USER_ID: 2})
    )
    coordinator._async_handle_packet(WEIGHT, SCALE_WEIGHT_UUID)
    coordinator._async_handle_packet(BODY, SCALE_BODY_UUID)
    assert coordinator.data.body_fat == 18.4
    # A newer record from another user must not affect this person's sensors.
    coordinator._async_handle_packet(
        pack("<BHxxIxxxxB", 0x1D, 8000, 532000002, 1), SCALE_WEIGHT_UUID
    )
    assert coordinator.data.weight == 78.2
    coordinator._async_handle_packet(
        pack("<BHxxIxxxxB", 0x1D, 7900, 532000001, 2), SCALE_WEIGHT_UUID
    )
    assert coordinator.data.weight == 79 and coordinator.data.body_fat is None
    coordinator._async_handle_packet(WEIGHT, SCALE_WEIGHT_UUID)
    assert coordinator.data.weight == 79
    coordinator._async_handle_packet(b"\xff", SCALE_WEIGHT_UUID)
    assert coordinator.data.weight == 79


async def test_glucose_sequence_is_used_even_when_device_clock_moves_backwards(hass):
    coordinator = MedisanaCoordinator(hass, entry_for(hass, DEVICE_TYPE_GLUCOSE))
    first = bytes([2]) + GLUCOSE[1:]
    coordinator._async_handle_packet(first, GLUCOSE_MEASUREMENT_UUID)
    earlier = (
        pack("<BH", 2, 2)
        + pack("<HBBBBB", 2026, 10, 1, 1, 0, 0)
        + pack("<HB", 0xB065, 0x11)
    )
    coordinator._async_handle_packet(earlier, GLUCOSE_MEASUREMENT_UUID)
    assert coordinator.data.glucose == 101 and coordinator.data.sequence_number == 2
    coordinator._async_handle_packet(first, GLUCOSE_MEASUREMENT_UUID)
    assert coordinator.data.sequence_number == 2
    sequence = coordinator.measurement_sequence
    coordinator._async_handle_packet(earlier, GLUCOSE_MEASUREMENT_UUID)
    assert coordinator.measurement_sequence == sequence


async def test_glucose_without_racp_can_receive_automatic_transfer(hass):
    coordinator = MedisanaCoordinator(hass, entry_for(hass, DEVICE_TYPE_GLUCOSE))
    client, _ = client_for({GLUCOSE_MEASUREMENT_UUID: ["notify"]})
    coordinator._client = client
    assert await coordinator._async_subscribe()
    client.write_gatt_char.assert_not_awaited()
    coordinator._async_handle_packet(bytes([2]) + GLUCOSE[1:])
    assert coordinator.data.glucose == 100


async def test_glucose_racp_authentication_failure_keeps_live_subscription(hass):
    coordinator = MedisanaCoordinator(hass, entry_for(hass, DEVICE_TYPE_GLUCOSE))
    client, _ = client_for(
        {
            GLUCOSE_MEASUREMENT_UUID: ["notify"],
            RECORD_ACCESS_CONTROL_UUID: ["write", "indicate"],
        }
    )
    coordinator._client = client
    client.write_gatt_char.side_effect = BleakError("Authentication required")
    assert await coordinator._async_subscribe()
    coordinator._async_handle_packet(bytes([2]) + GLUCOSE[1:])
    assert coordinator.data.glucose == 100


async def test_scale_wrong_protocol_is_rejected_before_writing(hass):
    coordinator = MedisanaCoordinator(
        hass, entry_for(hass, DEVICE_TYPE_SCALE, options={CONF_SCALE_EPOCH: "1970"})
    )
    client, _ = client_for({SCALE_WEIGHT_UUID: ["notify"]})
    coordinator._client = client
    assert not await coordinator._async_subscribe()
    client.write_gatt_char.assert_not_awaited()


async def test_continuous_only_oximeter_is_supported(hass):
    coordinator = MedisanaCoordinator(hass, entry_for(hass, DEVICE_TYPE_PULSE_OXIMETER))
    client, _ = client_for({PLX_CONTINUOUS_UUID: ["notify"]})
    coordinator._client = client
    assert await coordinator._async_subscribe()
    coordinator._async_handle_packet(b"\x00" + pack("<HH", 98, 60), PLX_CONTINUOUS_UUID)
    assert coordinator.data.measurement_type == "continuous"
    assert coordinator.data.oxygen_saturation == 98


async def test_scale_waits_for_composition_before_connecting(hass):
    coordinator = MedisanaCoordinator(hass, entry_for(hass, DEVICE_TYPE_SCALE))
    events = []

    async def sleep(delay):
        events.append(("wait", delay))

    async def connect(*args, **kwargs):
        events.append(("connect", None))
        raise BleakError("Device asleep")

    with (
        patch(f"{MODULE}.asyncio.sleep", side_effect=sleep),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        await coordinator._async_listen()
    assert events == [("wait", 12), ("connect", None)]


@pytest.mark.parametrize(
    "enabled,clock_properties",
    [(False, ["write"]), (True, ["write"]), (True, ["read"]), (True, None)],
)
async def test_blood_pressure_clock_sync_is_optional_and_follows_subscription(
    hass, enabled, clock_properties
):
    from datetime import UTC, datetime

    await hass.config.async_set_time_zone("Europe/Zurich")
    coordinator = MedisanaCoordinator(
        hass,
        entry_for(hass, DEVICE_TYPE_BLOOD_PRESSURE, options={CONF_SYNC_TIME: enabled}),
    )
    uuids = {BLOOD_PRESSURE_CHARACTERISTIC_UUID: ["indicate"]}
    if clock_properties is not None:
        uuids[CURRENT_TIME_UUID] = clock_properties
    client, _ = client_for(uuids)
    coordinator._client = client
    with patch(
        f"{MODULE}.dt_util.utcnow",
        return_value=datetime(2026, 11, 15, 8, 30, tzinfo=UTC),
    ):
        assert await coordinator._async_subscribe()
    if enabled and clock_properties == ["write"]:
        assert [
            call[0]
            for call in client.mock_calls
            if call[0] in ("start_notify", "write_gatt_char")
        ] == ["start_notify", "write_gatt_char"]
        assert client.write_gatt_char.await_args.args[1] == pack(
            "<H8B", 2026, 11, 15, 9, 30, 0, 7, 0, 2
        )
    else:
        client.write_gatt_char.assert_not_awaited()
