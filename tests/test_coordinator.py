"""Connection lifecycle and measurement acceptance tests."""

import asyncio
from datetime import datetime
from struct import pack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medisana_ble.const import DEVICE_TYPE_THERMOMETER, DOMAIN
from custom_components.medisana_ble.coordinator import MedisanaCoordinator

ADDRESS = "AA:BB:CC:DD:EE:FF"
MODULE = "custom_components.medisana_ble.coordinator"


def packet(*, user=1, day=16, systolic=120):
    """An entire Blood Pressure Measurement including optional fields."""
    return pack(
        "<BHHHHBBBBBH BH", 0x1E, systolic, 80, 93, 2026, 9, day, 12, 30, 0, 65, user, 0
    )


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="BU-570",
        data={"address": ADDRESS},
    )
    entry.add_to_hass(hass)
    return MedisanaCoordinator(hass, entry)


async def test_packet_publishes_without_waiting_for_disconnect(coordinator):
    listener = MagicMock()
    unsubscribe = coordinator.async_add_listener(listener)
    coordinator._async_handle_packet(packet())
    assert coordinator.data.systolic == 120
    assert coordinator.data.pulse == 65
    assert coordinator.data.timestamp == datetime(2026, 9, 16, 12, 30)
    assert coordinator.received_at.tzinfo is not None
    listener.assert_called_once()
    unsubscribe()


async def test_bad_packet_does_not_replace_last_reading(coordinator):
    coordinator._async_handle_packet(packet())
    previous = coordinator.data
    coordinator._async_handle_packet(b"\x1e\x00")
    assert coordinator.data is previous


async def test_user_filter_including_zero_and_absent_user(coordinator):
    coordinator.user_id_filter = 0
    coordinator._async_handle_packet(packet(user=1))
    coordinator._async_handle_packet(pack("<BHHH", 0, 120, 80, 93))
    assert coordinator.data is None
    coordinator._async_handle_packet(packet(user=0))
    assert coordinator.data.user_id == 0


async def test_old_records_and_duplicates_do_not_roll_back(coordinator):
    coordinator._async_handle_packet(packet())
    received_at = coordinator.received_at
    coordinator._async_handle_packet(packet(day=15, systolic=150))
    coordinator._async_handle_packet(packet())
    assert coordinator.data.systolic == 120
    assert coordinator.received_at == received_at


async def test_shutdown_ignores_late_packet(coordinator):
    await coordinator.async_stop()
    coordinator._async_handle_packet(packet())
    assert coordinator.data is None


async def test_sleeping_device_does_not_attempt_connection(coordinator):
    with (
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=False),
        patch.object(coordinator, "_async_listen", new_callable=AsyncMock) as listen,
    ):
        coordinator._async_maybe_connect()
        listen.assert_not_called()


async def test_one_session_at_a_time_and_cancellation_cleanup(hass, coordinator):
    client = MagicMock()
    client.disconnect = AsyncMock()
    subscribed = asyncio.Event()
    characteristic = MagicMock(properties=["indicate"])
    client.services.get_characteristic.return_value = characteristic

    async def subscribe(*args):
        subscribed.set()

    client.start_notify = AsyncMock(side_effect=subscribe)
    with (
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", return_value=client) as connect,
    ):
        coordinator._async_maybe_connect()
        coordinator._async_maybe_connect()
        await asyncio.wait_for(subscribed.wait(), 1)
        connect.assert_awaited_once()
        client.start_notify.assert_awaited_once_with(
            characteristic, coordinator._notification
        )
        await coordinator.async_stop()
        client.disconnect.assert_awaited_once()
        assert coordinator._task is None
        assert coordinator._client is None


async def test_disconnect_keeps_reading_and_allows_later_reconnect(coordinator):
    client = MagicMock()
    client.disconnect = AsyncMock()
    client.services.get_characteristic.return_value = MagicMock(properties=["indicate"])

    disconnect_callback = None

    async def connect(*args, **kwargs):
        nonlocal disconnect_callback
        disconnect_callback = kwargs["disconnected_callback"]
        return client

    async def subscribe(*args):
        coordinator._notification(MagicMock(), bytearray(packet()))
        disconnect_callback(client)

    client.start_notify = AsyncMock(side_effect=subscribe)
    with (
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        await coordinator._async_listen()
    assert coordinator.data.systolic == 120
    assert coordinator._next_attempt > 0
    client.disconnect.assert_awaited_once()


async def test_battery_level_and_device_info_are_read_when_available(coordinator):
    client = MagicMock()
    client.disconnect = AsyncMock()

    def get_characteristic(uuid):
        characteristic = MagicMock(properties=["indicate"])
        characteristic.uuid = uuid
        return characteristic

    client.services.get_characteristic.side_effect = lambda uuid: (
        get_characteristic(uuid)
        if uuid == "00002a35-0000-1000-8000-00805f9b34fb"
        else get_characteristic(uuid)
    )
    client.read_gatt_char = AsyncMock(
        side_effect=[
            b"\x5a",
            b"Medisana",
            b"BU-570",
            b"1.2.3",
            b"serial-123",
            b"rev-2",
        ]
    )
    disconnected = None

    async def connect(*args, **kwargs):
        nonlocal disconnected
        disconnected = kwargs["disconnected_callback"]
        return client

    async def subscribe(*args):
        coordinator._notification(MagicMock(), bytearray(packet()))
        disconnected(client)

    client.start_notify = AsyncMock(side_effect=subscribe)
    with (
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(
            f"{MODULE}.establish_connection",
            side_effect=connect,
        ),
    ):
        await coordinator._async_listen()

    assert coordinator.battery_level == 90
    assert coordinator.device_info["manufacturer_name"] == "Medisana"
    assert coordinator.device_info["model_number"] == "BU-570"
    assert coordinator.device_info["firmware_revision"] == "1.2.3"
    assert coordinator.device_info["serial_number"] == "serial-123"
    assert coordinator.device_info["hardware_revision"] == "rev-2"


@pytest.mark.parametrize(
    "error", [BleakError("out of slots"), TimeoutError(), OSError()]
)
async def test_connection_failure_can_retry(coordinator, error):
    with (
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=error),
    ):
        await coordinator._async_listen()
    assert coordinator._task is None
    assert coordinator.data is None
    assert coordinator._next_attempt > 0


async def test_unsupported_profile_releases_connection(coordinator):
    client = MagicMock()
    client.services.get_characteristic.return_value = None
    client.disconnect = AsyncMock()
    with (
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", return_value=client),
    ):
        await coordinator._async_listen()
    client.disconnect.assert_awaited_once()
    assert coordinator.data is None


async def test_start_registers_retry_and_stop_removes_callbacks(coordinator):
    unsub_advertisements = MagicMock()
    unsub_timer = MagicMock()
    with (
        patch(
            f"{MODULE}.bluetooth.async_register_callback",
            return_value=unsub_advertisements,
        ),
        patch(f"{MODULE}.async_track_time_interval", return_value=unsub_timer),
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=False),
    ):
        coordinator.async_start()
        await coordinator.async_stop()
        await coordinator.async_stop()
    unsub_advertisements.assert_called_once()
    unsub_timer.assert_called_once()


async def test_failed_attempt_disconnect_does_not_end_successful_retry(coordinator):
    """An internal connection retry cannot leave a stale disconnection event."""
    client = MagicMock()
    client.disconnect = AsyncMock()
    client.services.get_characteristic.return_value = MagicMock(properties=["indicate"])
    subscribed = asyncio.Event()

    async def connect(*args, **kwargs):
        kwargs["disconnected_callback"](client)
        return client

    async def subscribe(*args):
        subscribed.set()

    client.start_notify = AsyncMock(side_effect=subscribe)
    with (
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        coordinator._async_maybe_connect()
        await asyncio.wait_for(subscribed.wait(), 1)
        await asyncio.sleep(0)
        assert coordinator._task is not None and not coordinator._task.done()
        client.disconnect.assert_not_awaited()
        await coordinator.async_stop()
    client.disconnect.assert_awaited_once()


async def test_multiple_records_and_repeated_sessions(coordinator):
    """Keep receiving after the first packet, including on the next connection."""
    subscribed = asyncio.Event()
    clients = []
    disconnect_callbacks = []

    async def connect(*args, **kwargs):
        client = MagicMock()
        client.disconnect = AsyncMock()
        client.services.get_characteristic.return_value = MagicMock(
            properties=["indicate"]
        )
        client.start_notify = AsyncMock(side_effect=lambda *args: subscribed.set())
        clients.append(client)
        disconnect_callbacks.append(kwargs["disconnected_callback"])
        return client

    with (
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(
            f"{MODULE}.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(f"{MODULE}.establish_connection", side_effect=connect),
    ):
        for index in range(2):
            subscribed.clear()
            coordinator._next_attempt = 0
            coordinator._async_maybe_connect()
            task = coordinator._task
            await asyncio.wait_for(subscribed.wait(), 1)
            notify = clients[index].start_notify.call_args.args[1]
            notify(MagicMock(), bytearray(packet(day=16 + index, systolic=120 + index)))
            await asyncio.sleep(0)
            assert not task.done()
            # A later record in the same transfer must still be received.
            notify(MagicMock(), bytearray(packet(day=16 + index, systolic=125 + index)))
            await asyncio.sleep(0)
            assert coordinator.data.systolic == 125 + index
            clients[index].read_gatt_char.assert_not_called()
            disconnect_callbacks[index](clients[index])
            await asyncio.wait_for(task, 1)
            clients[index].disconnect.assert_awaited_once()
    assert clients[0] is not clients[1]


async def test_cancel_before_connection_coroutine_starts(coordinator):
    """Unload immediately after setup must also clear the scheduled task."""
    with (
        patch(f"{MODULE}.bluetooth.async_address_present", return_value=True),
        patch(f"{MODULE}.establish_connection") as connect,
    ):
        coordinator._async_maybe_connect()
        await coordinator.async_stop()
    connect.assert_not_called()
    assert coordinator._task is None
