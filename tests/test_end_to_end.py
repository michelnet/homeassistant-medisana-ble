"""Simulate discovery through GATT reception using real Home Assistant entities.

Packets are synthetic examples of the standard Bluetooth layout. They are not
recorded BU-570 packets or real health readings.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.match import IntegrationMatcher
from homeassistant.config_entries import SOURCE_BLUETOOTH, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.medisana_ble.const import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    BLOOD_PRESSURE_CHARACTERISTIC_UUID,
    DEVICE_INFORMATION_CHARACTERISTIC_UUIDS,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
    HEALTH_THERMOMETER_SERVICE_UUID,
    TEMPERATURE_MEASUREMENT_UUID,
)

pytestmark = pytest.mark.usefixtures("mock_bluetooth")
ADDRESS = "AA:BB:CC:DD:EE:FF"
MODULE = "custom_components.medisana_ble.coordinator"

# 2026-09-17 08:15:30, 120/80 mmHg, MAP 93, pulse 65, user 1, status 0.
FIRST_PACKET = bytes.fromhex("1e 78 00 50 00 5d 00 ea 07 09 11 08 0f 1e 41 00 01 00 00")
# One minute later: 118/78 mmHg, MAP 91, pulse 64, user 1, status 0.
SECOND_PACKET = bytes.fromhex(
    "1e 76 00 4e 00 5b 00 ea 07 09 11 08 10 1e 40 00 01 00 00"
)


async def test_discovery_measurements_and_reconnection(hass):
    """Discover, configure, receive all fields, sleep and receive a second record."""
    await hass.config.async_set_time_zone("Europe/Zurich")
    device = BLEDevice(ADDRESS, "1872B", {})
    advertisement = AdvertisementData(
        local_name="1872B",
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    info = BluetoothServiceInfoBleak(
        name="1872B",
        address=ADDRESS,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="test-adapter",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=None,
    )
    manifest = json.loads(
        (
            Path(__file__).parents[1] / "custom_components/medisana_ble/manifest.json"
        ).read_text()
    )
    matcher = IntegrationMatcher(
        [{**item, "domain": DOMAIN} for item in manifest["bluetooth"]]
    )
    matcher.async_setup()
    assert matcher.match_domains(info) == {DOMAIN}

    characteristic = MagicMock(uuid=BLOOD_PRESSURE_CHARACTERISTIC_UUID)
    characteristic.properties = ["indicate"]
    subscribed = asyncio.Event()
    sessions = []

    async def connect(*args, **kwargs):
        client = MagicMock()
        client.is_connected = True
        client.services.get_characteristic.return_value = characteristic
        client.disconnect = AsyncMock()
        client.start_notify = AsyncMock(side_effect=lambda *args: subscribed.set())
        sessions.append((client, kwargs["disconnected_callback"]))
        return client

    with (
        patch(f"{MODULE}.bluetooth.async_register_callback", return_value=MagicMock()),
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(f"{MODULE}.bluetooth.async_ble_device_from_address", return_value=device),
        patch(f"{MODULE}.establish_connection", side_effect=connect) as establish,
        patch(f"{MODULE}.monotonic", return_value=100.0) as monotonic,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
        )
        assert result["step_id"] == "bluetooth_confirm"
        establish.assert_not_called()
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        await asyncio.wait_for(subscribed.wait(), 1)
        entry = result["result"]
        assert entry.state is ConfigEntryState.LOADED
        coordinator = entry.runtime_data
        entities = {
            entity.unique_id.removeprefix(f"{ADDRESS.lower()}_"): entity.entity_id
            for entity in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
        }
        assert len(entities) == 8
        assert all(
            hass.states.get(entity).state == "unknown" for entity in entities.values()
        )

        for index, payload in enumerate((FIRST_PACKET, SECOND_PACKET)):
            client, disconnected = sessions[index]
            client.services.get_characteristic.assert_any_call(
                BLOOD_PRESSURE_CHARACTERISTIC_UUID
            )
            notify = client.start_notify.call_args.args[1]
            notify(characteristic, bytearray(payload))
            await hass.async_block_till_done()
            expected = {
                "systolic": 120 if index == 0 else 118,
                "diastolic": 80 if index == 0 else 78,
                "mean_arterial_pressure": 93 if index == 0 else 91,
                "pulse": 65 if index == 0 else 64,
                "user_id": 1,
            }
            for key, value in expected.items():
                assert float(hass.states.get(entities[key]).state) == value
            assert hass.states.get(entities["last_measurement"]).state == (
                f"2026-09-17T06:{15 + index}:30+00:00"
            )
            assert hass.states.get(entities["last_received"]).state != "unknown"
            assert (
                hass.states.get(entities["systolic"]).attributes["measurement_status"]
                == 0
            )
            client.disconnect.assert_not_awaited()

            task = coordinator._task
            disconnected(client)
            await asyncio.wait_for(task, 1)
            client.disconnect.assert_awaited_once()
            assert (
                float(hass.states.get(entities["systolic"]).state)
                == expected["systolic"]
            )
            if index == 0:
                subscribed.clear()
                monotonic.return_value = 116.0
                coordinator._async_maybe_connect()
                await asyncio.wait_for(subscribed.wait(), 1)

        assert establish.await_count == 2
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert coordinator._stopped
        assert coordinator._task is None


async def test_thermometer_discovery_measurement_and_device_information(hass):
    """Exercise the full thermometer path, including optional GATT metadata."""
    await hass.config.async_set_time_zone("Europe/Zurich")
    device = BLEDevice(ADDRESS, "TS42B", {})
    advertisement = AdvertisementData(
        local_name="TS42B",
        manufacturer_data={},
        service_data={},
        service_uuids=[HEALTH_THERMOMETER_SERVICE_UUID],
        tx_power=None,
        rssi=-55,
        platform_data=(),
    )
    info = BluetoothServiceInfoBleak(
        name="TS42B",
        address=ADDRESS,
        rssi=-55,
        manufacturer_data={},
        service_data={},
        service_uuids=[HEALTH_THERMOMETER_SERVICE_UUID],
        source="test-adapter",
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=0,
        tx_power=None,
    )
    measurement_characteristic = MagicMock(
        uuid=TEMPERATURE_MEASUREMENT_UUID, properties=["indicate"]
    )
    values = {
        BATTERY_LEVEL_CHARACTERISTIC_UUID: b"\x58",
        DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["manufacturer_name"]: b"Medisana",
        DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["model_number"]: b"TM 750 connect",
        DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["firmware_revision"]: b"1.2.3",
        DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["serial_number"]: b"TM750-123",
        DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["hardware_revision"]: b"2.0",
    }
    characteristics = {
        uuid: MagicMock(uuid=uuid, properties=["read"]) for uuid in values
    }
    characteristics[TEMPERATURE_MEASUREMENT_UUID] = measurement_characteristic
    subscribed = asyncio.Event()
    metadata_read = asyncio.Event()
    disconnected_callback = None
    client = MagicMock(is_connected=True)
    client.services.get_characteristic.side_effect = characteristics.get
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock(side_effect=lambda *args: subscribed.set())

    async def read_characteristic(characteristic):
        value = values[characteristic.uuid]
        if (
            characteristic.uuid
            == DEVICE_INFORMATION_CHARACTERISTIC_UUIDS["hardware_revision"]
        ):
            metadata_read.set()
        return value

    client.read_gatt_char = AsyncMock(side_effect=read_characteristic)

    async def connect(*args, **kwargs):
        nonlocal disconnected_callback
        disconnected_callback = kwargs["disconnected_callback"]
        return client

    with (
        patch(f"{MODULE}.bluetooth.async_register_callback", return_value=MagicMock()),
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(f"{MODULE}.bluetooth.async_ble_device_from_address", return_value=device),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.data["device_type"] == DEVICE_TYPE_THERMOMETER
        await asyncio.wait_for(subscribed.wait(), 1)
        await asyncio.wait_for(metadata_read.wait(), 1)
        await hass.async_block_till_done()

        entities = {
            entity.unique_id.removeprefix(f"{ADDRESS.lower()}_"): entity.entity_id
            for entity in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
        }
        assert set(entities) == {
            "temperature",
            "battery_level",
            "last_measurement",
            "last_received",
        }
        assert hass.states.get(entities["battery_level"]).state == "88"

        # 2026-09-17 08:15:30, 36.5 °C, body temperature type.
        payload = bytes.fromhex("06 6d 01 00 ff ea 07 09 11 08 0f 1e 02")
        notify = client.start_notify.call_args.args[1]
        notify(measurement_characteristic, bytearray(payload))
        await hass.async_block_till_done()

        temperature = hass.states.get(entities["temperature"])
        assert float(temperature.state) == 36.5
        assert temperature.attributes["unit_of_measurement"] == "°C"
        assert temperature.attributes["temperature_type"] == 2
        assert hass.states.get(entities["last_measurement"]).state == (
            "2026-09-17T06:15:30+00:00"
        )
        assert hass.states.get(entities["last_received"]).state != "unknown"

        registry_device = dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )[0]
        assert registry_device.manufacturer == "Medisana"
        assert registry_device.model == "TM 750 connect"
        assert registry_device.serial_number == "TM750-123"
        assert registry_device.sw_version == "1.2.3"
        assert registry_device.hw_version == "2.0"

        task = entry.runtime_data._task
        disconnected_callback(client)
        await asyncio.wait_for(task, 1)
        assert hass.states.get(entities["temperature"]).state == "36.5"
        client.disconnect.assert_awaited_once()
        assert await hass.config_entries.async_unload(entry.entry_id)
