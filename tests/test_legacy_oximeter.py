"""Older PM 150 protocol facts, with simulated GATT and synthetic packets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.medisana_ble.config_flow import get_device_type
from custom_components.medisana_ble.const import (
    DEVICE_TYPE_PULSE_OXIMETER,
    DOMAIN,
    LEGACY_OXIMETER_CONTROL_UUIDS,
    LEGACY_OXIMETER_MEASUREMENT_UUID,
    LEGACY_OXIMETER_SERVICE_PREFIX,
    LEGACY_OXIMETER_START,
    LEGACY_OXIMETER_WRITE_UUID,
)
from custom_components.medisana_ble.coordinator import MedisanaCoordinator
from custom_components.medisana_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.medisana_ble.pulse_oximeter import parse_legacy_oximeter

SERVICE = LEGACY_OXIMETER_SERVICE_PREFIX + "1080-007cbe3d328f"
ADDRESS = "00:7C:BE:3D:32:8F"


def test_legacy_field_offsets_and_unavailable_values():
    # Three uninterpreted vendor bytes, then SpO2 and pulse. No verified
    # frame header/status meaning is inferred from these synthetic fixtures.
    value = parse_legacy_oximeter(bytes([0, 0, 0, 97, 91]))
    assert value.oxygen_saturation == 97 and value.pulse == 91
    assert value.timestamp is None and value.measurement_type == "legacy_cd04"
    assert value.measurement_status is None
    for saturation, pulse in [(0, 0), (255, 255), (101, 255)]:
        value = parse_legacy_oximeter(bytes([0, 0, 0, saturation, pulse]))
        assert value.oxygen_saturation is None and value.pulse is None
    for length in [0, 1, 2, 3, 4, 21]:
        with pytest.raises(ValueError):
            parse_legacy_oximeter(bytes(length))


def test_legacy_discovery_uses_variable_uuid_suffix_and_connectable_flag():
    info = SimpleNamespace(
        connectable=True,
        service_uuids=[SERVICE.upper()],
        name="PM 150",
        manufacturer_data={},
    )
    assert get_device_type(info) == DEVICE_TYPE_PULSE_OXIMETER
    info.name = "Sugar Watch"
    assert get_device_type(info) is None
    info.name = "PM 150"
    info.connectable = False
    assert get_device_type(info) is None


@pytest.mark.parametrize("broken", [None, "service", "write", "control", "measurement"])
async def test_legacy_session_requires_complete_service_and_subscribes_before_start(
    hass, broken
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PM 150",
        data={"address": ADDRESS, "device_type": DEVICE_TYPE_PULSE_OXIMETER},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator = MedisanaCoordinator(hass, entry)
    uuids = (*LEGACY_OXIMETER_CONTROL_UUIDS, LEGACY_OXIMETER_MEASUREMENT_UUID)
    chars = {uuid: SimpleNamespace(uuid=uuid, properties=["notify"]) for uuid in uuids}
    chars[LEGACY_OXIMETER_WRITE_UUID] = SimpleNamespace(
        uuid=LEGACY_OXIMETER_WRITE_UUID, properties=["write-without-response"]
    )
    if broken == "write":
        chars[LEGACY_OXIMETER_WRITE_UUID].properties = ["read"]
    elif broken == "control":
        del chars[LEGACY_OXIMETER_CONTROL_UUIDS[1]]
    elif broken == "measurement":
        chars[LEGACY_OXIMETER_MEASUREMENT_UUID].properties = ["read"]
    service = SimpleNamespace(
        uuid=SERVICE if broken != "service" else "0000fff0-0000-1000-8000-00805f9b34fb",
        characteristics=list(chars.values()),
    )
    client = MagicMock()
    client.services.__iter__.return_value = [service]
    client.services.get_characteristic.side_effect = chars.get
    callbacks = {}

    async def subscribe(characteristic, callback):
        callbacks[characteristic.uuid] = callback

    async def write(characteristic, data, *, response):
        assert list(callbacks) == list(uuids)
        assert characteristic.uuid == LEGACY_OXIMETER_WRITE_UUID
        assert data == LEGACY_OXIMETER_START and not response

    client.start_notify = AsyncMock(side_effect=subscribe)
    client.write_gatt_char = AsyncMock(side_effect=write)
    coordinator._client = client
    assert await coordinator._async_subscribe() is (broken is None)
    if broken is not None:
        client.write_gatt_char.assert_not_awaited()
        client.start_notify.assert_not_awaited()
    else:
        for uuid in LEGACY_OXIMETER_CONTROL_UUIDS:
            callbacks[uuid](chars[uuid], bytearray([0, 0, 0, 99, 80]))
        assert coordinator.data is None
        callbacks[LEGACY_OXIMETER_MEASUREMENT_UUID](
            chars[LEGACY_OXIMETER_MEASUREMENT_UUID], bytearray([0, 0, 0, 97, 91])
        )
        await hass.async_block_till_done()
        assert coordinator.data.oxygen_saturation == 97
        assert coordinator.data.pulse == 91
        assert coordinator.oximeter_protocol == "legacy_cd04"
        coordinator.gatt_endpoints = [
            {"service": SERVICE, "characteristic": uuid, "properties": ["notify"]}
            for uuid in uuids
        ]
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert SERVICE not in str(diagnostics) and "007cbe3d328f" not in str(
            diagnostics
        )
    coordinator._client = None
    await coordinator.async_stop()
