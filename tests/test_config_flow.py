"""Verify automatic discovery, setup fallback and user options."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.match import IntegrationMatcher
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medisana_ble.config_flow import (
    MedisanaConfigFlow,
    get_device_type,
)
from custom_components.medisana_ble.const import (
    BLOOD_PRESSURE_SERVICE_UUID,
    CONF_DEVICE_TYPE,
    CONF_USER_ID,
    DEFAULT_NAME,
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
    HEALTH_THERMOMETER_SERVICE_UUID,
)

pytestmark = pytest.mark.usefixtures("mock_bluetooth")
ADDRESS = "AA:BB:CC:DD:EE:FF"
THERMOMETER_ADDRESS = "11:22:33:44:55:66"


@pytest.fixture(autouse=True)
def mock_setup_entry():
    with patch(
         "custom_components.medisana_ble.async_setup_entry", return_value=True
     ):
        pass

def discovery(signature="service", *, connectable=True, address=ADDRESS):
    name = {
        "name": "1872B",
        "thermometer_name": "TS42B",
        "thermometer_service": "Thermometer",
        "thermometer_shared_id": "TS42B",
    }.get(signature, "BP Monitor")
    manufacturer_data = {signature: b"\x01"} if isinstance(signature, int) else {}
    service_uuids = [BLOOD_PRESSURE_SERVICE_UUID] if signature == "service" else []
    if signature == "thermometer_service":
        service_uuids = [HEALTH_THERMOMETER_SERVICE_UUID]
    elif signature == "thermometer_shared_id":
        manufacturer_data = {18498: b"\x01"}
    adv = AdvertisementData(
        local_name=name,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=service_uuids,
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=service_uuids,
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=adv,
        connectable=connectable,
        time=0,
        tx_power=None,
    )


def _entry(device_type=DEVICE_TYPE_BLOOD_PRESSURE, *, address=ADDRESS):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Medisana",
        unique_id=address,
        data={CONF_ADDRESS: address, CONF_DEVICE_TYPE: device_type},
    )


async def open_manual(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )


@pytest.mark.parametrize(
    "address", ["aa:bb:cc:dd:ee:ff", "aa-bb-cc-dd-ee-ff", " aabbccddeeff "]
)
@pytest.mark.parametrize(
    "device_type", [DEVICE_TYPE_BLOOD_PRESSURE, DEVICE_TYPE_THERMOMETER]
)
async def test_manual_setup_while_asleep(hass, address, device_type):
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        side_effect=AssertionError("Setup must not connect"),
    ):
        result = await open_manual(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ADDRESS: address,
                CONF_NAME: " My Medisana ",
                CONF_DEVICE_TYPE: device_type,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Medisana"
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_TYPE: device_type,
    }
    assert result["result"].unique_id == ADDRESS
    mock_setup_entry.assert_awaited_once()


async def test_manual_defaults_to_blood_pressure(hass):
    """Manual setup uses a translated device selector and the combined name."""
    result = await open_manual(hass)
    assert result["data_schema"]({CONF_ADDRESS: ADDRESS}) == {
        CONF_ADDRESS: ADDRESS,
        CONF_DEVICE_TYPE: DEVICE_TYPE_BLOOD_PRESSURE,
        CONF_NAME: DEFAULT_NAME,
    }
    device_selector = next(
        value
        for key, value in result["data_schema"].schema.items()
        if key == CONF_DEVICE_TYPE
    )
    assert device_selector.config["translation_key"] == CONF_DEVICE_TYPE
    assert device_selector.config["options"] == [
        DEVICE_TYPE_BLOOD_PRESSURE,
        DEVICE_TYPE_THERMOMETER,
    ]


@pytest.mark.parametrize(
    "address", ["invalid", "aa:bb:cc:dd:ee", "aa:bb-cc:dd:ee:ff", "GG:BB:CC:DD:EE:FF"]
)
async def test_invalid_mac_then_correct(hass, address):
    result = await open_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: address, CONF_NAME: "Medisana"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_NAME: "Medisana"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_empty_device_name(hass):
    result = await open_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_NAME: "   "}
    )
    assert result["errors"] == {CONF_NAME: "invalid_name"}


@pytest.mark.parametrize(
    "device_type", [DEVICE_TYPE_BLOOD_PRESSURE, DEVICE_TYPE_THERMOMETER]
)
async def test_duplicate_manual_device(hass, device_type):
    _entry().add_to_hass(hass)
    result = await open_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: ADDRESS.lower(),
            CONF_NAME: "Duplicate",
            CONF_DEVICE_TYPE: device_type,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("signature", "device_type"),
    [
        (18498, DEVICE_TYPE_BLOOD_PRESSURE),
        (31256, DEVICE_TYPE_BLOOD_PRESSURE),
        ("name", DEVICE_TYPE_BLOOD_PRESSURE),
        ("service", DEVICE_TYPE_BLOOD_PRESSURE),
        ("thermometer_name", DEVICE_TYPE_THERMOMETER),
        ("thermometer_service", DEVICE_TYPE_THERMOMETER),
        ("thermometer_shared_id", DEVICE_TYPE_THERMOMETER),
    ],
)
async def test_automatic_discovery_from_real_manifest(hass, signature, device_type):
    """Exercise HA's matcher, including devices with no advertised service UUID."""
    manifest = json.loads(
        (
            Path(__file__).parents[1] / "custom_components/medisana_ble/manifest.json"
        ).read_text()
    )
    matcher = IntegrationMatcher(
        [{**item, "domain": DOMAIN} for item in manifest["bluetooth"]]
    )
    matcher.async_setup()
    info = discovery(signature)
    assert matcher.match_domains(info) == {DOMAIN}
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        side_effect=AssertionError("Discovery must not connect"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "bluetooth_confirm"
        assert result["description_placeholders"]["address"] == ADDRESS
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_DEVICE_TYPE: device_type}
    assert result["result"].unique_id == ADDRESS


@pytest.mark.parametrize("signature", [18498, "thermometer_name"])
async def test_duplicate_bluetooth_device(hass, signature):
    _entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=discovery(signature)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "signature,connectable",
    [
        (18498, False),
        ("thermometer_name", False),
        ("thermometer_service", False),
        ("unrelated", True),
    ],
)
async def test_reject_unrelated_or_nonconnectable_device(hass, signature, connectable):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery(signature, connectable=connectable),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_choose_discovered_device(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[discovery(31256)],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discovered"}
        )
    assert result["step_id"] == "discovered"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_choose_thermometer_from_mixed_discovery(hass):
    """List both types, exclude configured devices and keep the selected type."""
    configured_address = "CC:CC:CC:CC:CC:CC"
    _entry(address=configured_address).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[
            discovery(31256),
            discovery("thermometer_name", address=THERMOMETER_ADDRESS),
            discovery("service", address=configured_address),
            discovery("unrelated", address="DD:DD:DD:DD:DD:DD"),
            discovery(
                "thermometer_name", connectable=False, address="EE:EE:EE:EE:EE:EE"
            ),
        ],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discovered"}
        )
    address_selector = next(iter(result["data_schema"].schema.values()))
    assert set(address_selector.container) == {ADDRESS, THERMOMETER_ADDRESS}
    assert "Blood Pressure" in address_selector.container[ADDRESS]
    assert "TM 750" in address_selector.container[THERMOMETER_ADDRESS]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: THERMOMETER_ADDRESS}
    )
    assert result["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["data"] == {
        CONF_ADDRESS: THERMOMETER_ADDRESS,
        CONF_DEVICE_TYPE: DEVICE_TYPE_THERMOMETER,
    }


@pytest.mark.parametrize("signature", ["thermometer_name", "thermometer_service"])
def test_thermometer_signature_takes_precedence(signature):
    """Thermometers with shared manufacturer IDs must use the right protocol."""
    info = discovery(signature)
    info.manufacturer_data[18498] = b"\x01"
    assert get_device_type(info) == DEVICE_TYPE_THERMOMETER


def test_thermometer_service_is_case_insensitive():
    """Normalize service UUIDs before choosing the device protocol."""
    info = discovery("thermometer_service")
    info.service_uuids[:] = [HEALTH_THERMOMETER_SERVICE_UUID.upper()]
    assert get_device_type(info) == DEVICE_TYPE_THERMOMETER


async def test_no_devices_shows_actionable_message(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discovered"}
        )
    assert result["reason"] == "no_devices_found"


@pytest.mark.parametrize("user_id", [-1, 0, 2, 254])
async def test_blood_pressure_user_options(hass: HomeAssistant, user_id: int) -> None:
    """User zero remains a valid filter, while -1 means all users."""
    entry = _entry()
    entry.add_to_hass(hass)
    assert MedisanaConfigFlow.async_supports_options_flow(entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["data_schema"]({}) == {CONF_USER_ID: -1}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_USER_ID: user_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_USER_ID: user_id}
    assert isinstance(entry.options[CONF_USER_ID], int)


async def test_reject_fractional_user_id(hass: HomeAssistant) -> None:
    """Number selector keyboard entry must not silently truncate fractions."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_USER_ID: 1.5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_USER_ID: "invalid_user_id"}
    assert entry.options == {}


async def test_thermometer_has_no_user_options(hass: HomeAssistant) -> None:
    """Hide blood pressure options and reject direct thermometer options flows."""
    entry = _entry(DEVICE_TYPE_THERMOMETER)
    entry.add_to_hass(hass)
    assert not MedisanaConfigFlow.async_supports_options_flow(entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_options_available"
    assert entry.options == {}
