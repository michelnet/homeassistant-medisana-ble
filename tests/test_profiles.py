"""New BLE profiles: synthetic packets, unit conversion and record association.

These are protocol fixtures, not device captures or hardware validation.
"""

from datetime import UTC, datetime
from struct import pack

import pytest

from custom_components.medisana_ble.const import (
    SCALE_BODY_UUID,
    SCALE_USER_UUID,
    SCALE_WEIGHT_UUID,
)
from custom_components.medisana_ble.glucose import (
    GlucoseDecoder,
    parse_glucose,
    parse_glucose_context,
)
from custom_components.medisana_ble.pulse_oximeter import parse_plx
from custom_components.medisana_ble.scale import (
    ScaleDecoder,
    default_scale_epoch,
    scale_time_command,
)

DATE = pack("<HBBBBB", 2026, 11, 15, 8, 30, 0)


def test_plx_optional_field_offsets_and_decimal_values():
    packet = b"\x0f" + pack("<HH", 0xF3CF, 65) + DATE
    packet += pack("<H", 0x100) + b"\x01\x00\x00" + pack("<H", 0xF00C)
    result = parse_plx(packet)
    assert result.oxygen_saturation == 97.5
    assert result.pulse == 65
    assert result.timestamp == datetime(2026, 11, 15, 8, 30)
    assert result.measurement_status == 0x100
    assert result.device_sensor_status == 1
    assert result.pulse_amplitude_index == 1.2


def test_plx_continuous_does_not_confuse_normal_fast_slow_or_status():
    packet = b"\x1f" + pack("<HHHHHH", 97, 65, 82, 90, 98, 60)
    packet += pack("<H", 0x100) + b"\x01\x00\x00" + pack("<H", 0xF00C)
    result = parse_plx(packet, continuous=True)
    assert (result.oxygen_saturation, result.pulse) == (97, 65)
    assert result.pulse_amplitude_index == 1.2
    assert result.measurement_type == "continuous"
    assert result.timestamp is None


@pytest.mark.parametrize("status", [0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000])
def test_plx_invalid_or_test_measurements_have_no_numeric_values(status):
    result = parse_plx(b"\x02" + pack("<HHH", 97, 65, status))
    assert result.oxygen_saturation is None and result.pulse is None
    assert result.measurement_status == status


@pytest.mark.parametrize("special", [0x07FE, 0x07FF, 0x0800, 0x0801, 0x0802])
def test_plx_special_values_are_unknown(special):
    result = parse_plx(b"\x00" + pack("<HH", special, special))
    assert result.oxygen_saturation is None and result.pulse is None


def test_plx_clock_unset_and_invalid_saturation():
    result = parse_plx(b"\x11" + pack("<HH", 150, 65) + DATE)
    assert result.timestamp is None and result.oxygen_saturation is None


@pytest.mark.parametrize("continuous", [False, True])
def test_plx_rejects_truncation_trailing_bytes_and_reserved_flags(continuous):
    packet = b"\x00" + pack("<HH", 97, 65)
    for malformed in [
        *(packet[:i] for i in range(5)),
        packet + b"\x00",
        b"\x20" + packet[1:],
    ]:
        with pytest.raises(ValueError):
            parse_plx(malformed, continuous=continuous)


def glucose_packet(sequence=1, *, context=False, offset=None, mol=False, status=None):
    flags = 2 | (16 if context else 0) | (1 if offset is not None else 0)
    flags |= (4 if mol else 0) | (8 if status is not None else 0)
    packet = pack("<BH", flags, sequence) + DATE
    if offset is not None:
        packet += pack("<h", offset)
    packet += pack("<HB", 0xC038 if mol else 0xB064, 0x11)
    if status is not None:
        packet += pack("<H", status)
    return packet


def test_glucose_si_units_and_time_offset():
    result = parse_glucose(glucose_packet(offset=-60))
    assert result.glucose == 100 and result.unit == "mg/dL"
    assert result.timestamp == datetime(2026, 11, 15, 7, 30)
    result = parse_glucose(glucose_packet(mol=True))
    assert result.glucose == 5.6 and result.unit == "mmol/L"
    decoder = GlucoseDecoder()
    decoder.measurement(glucose_packet(offset=90))
    assert decoder.measurement(glucose_packet(2)).timestamp.hour == 10


def test_glucose_context_matches_sequence_in_both_orders():
    decoder = GlucoseDecoder()
    assert decoder.measurement(glucose_packet(4, context=True)) is None
    assert decoder.context(pack("<BHB", 2, 3, 1)) is None
    result = decoder.context(pack("<BHB", 2, 4, 2))
    assert result.sequence_number == 4 and result.meal == 2
    result = decoder.measurement(glucose_packet(3, context=True))
    assert result.sequence_number == 3 and result.meal == 1


def test_glucose_context_all_fields_are_validated():
    # Extended flags, carbohydrate, meal, tester, exercise, medication and HbA1c.
    packet = pack("<BH", 0xFF, 42) + b"\x00" + b"\x01\x00\x00"
    packet += b"\x02\x11" + b"\x3c\x00\x64" + b"\x01\x00\x00" + b"\x00\x00"
    assert parse_glucose_context(packet) == (42, 2)
    for length in range(len(packet)):
        with pytest.raises(ValueError):
            parse_glucose_context(packet[:length])


def test_glucose_missing_value_and_fault_status():
    assert parse_glucose(b"\x00\x01\x00" + DATE).glucose is None
    assert parse_glucose(glucose_packet(status=1)).glucose == 100  # low battery
    assert parse_glucose(glucose_packet(status=2)).glucose is None
    assert parse_glucose(glucose_packet(status=0x400)).glucose is None
    assert parse_glucose(glucose_packet(status=0x800)).timestamp is None
    packet = glucose_packet()
    for malformed in [
        *(packet[:i] for i in range(len(packet))),
        packet + b"\x00",
        b"\xe0" + packet[1:],
    ]:
        with pytest.raises(ValueError):
            parse_glucose(malformed)


@pytest.mark.parametrize("sample", [0x1A, 0x41])
def test_glucose_control_solution_is_not_a_patient_reading(sample):
    packet = glucose_packet()[:-1] + bytes([sample])
    assert parse_glucose(packet).glucose is None


def scale_weight(user=2, seconds=100, value=7820):
    return pack("<BHxxIxxxxB", 0x1D, value, seconds, user)


def scale_body(user=2, seconds=100):
    return pack("<BIBHHHHH", 0x6F, seconds, user, 1700, 0xF0B8, 0xF258, 0xF1C2, 0xF01E)


@pytest.mark.parametrize("body_first", [False, True])
def test_scale_joins_body_weight_and_profile_for_same_user_and_time(body_first):
    decoder = ScaleDecoder("2010")
    decoder.feed(SCALE_USER_UUID, bytes.fromhex("84 00 02 00 01 32 b4 00 00"))
    if body_first:
        assert decoder.feed(SCALE_BODY_UUID, scale_body()) is None
        result = decoder.feed(SCALE_WEIGHT_UUID, scale_weight())
    else:
        decoder.feed(SCALE_WEIGHT_UUID, scale_weight())
        result = decoder.feed(SCALE_BODY_UUID, scale_body())
    assert result.weight == 78.2 and result.user_id == 2
    assert result.timestamp == datetime(2010, 1, 1, 0, 1, 40, tzinfo=UTC)
    assert (result.body_fat, result.body_water, result.muscle) == (18.4, 60, 45)
    assert result.bone_mass == 3 and result.basal_metabolism == 1700
    assert result.bmi == 24.1


def test_scale_never_merges_another_user_or_another_date():
    decoder = ScaleDecoder("1970")
    decoder.feed(SCALE_BODY_UUID, scale_body(user=1))
    decoder.feed(SCALE_BODY_UUID, scale_body(seconds=99))
    result = decoder.feed(SCALE_WEIGHT_UUID, scale_weight())
    assert result.body_fat is None
    decoder.feed(SCALE_BODY_UUID, scale_body())
    result = decoder.feed(SCALE_WEIGHT_UUID, scale_weight(seconds=101))
    assert result.body_fat is None


def test_scale_late_profile_adds_bmi_without_changing_reading_identity():
    decoder = ScaleDecoder("2010")
    weight = decoder.feed(SCALE_WEIGHT_UUID, scale_weight())
    decoder.feed(SCALE_WEIGHT_UUID, scale_weight(seconds=99, value=7900))
    assert weight.bmi is None
    result = decoder.feed(SCALE_USER_UUID, bytes.fromhex("84 00 02 00 01 32 b4 00 00"))
    assert result.bmi == 24.1 and result.timestamp == weight.timestamp


def test_scale_clock_variants_are_used_for_both_command_and_measurement():
    now = datetime(2026, 11, 15, 8, 30, tzinfo=UTC)
    for epoch in ("1970", "2010"):
        command = scale_time_command(now, epoch)
        assert command[0] == 2
        decoder = ScaleDecoder(epoch)
        result = decoder.feed(
            SCALE_WEIGHT_UUID,
            scale_weight(seconds=int.from_bytes(command[1:], "little")),
        )
        assert result.timestamp == now
    assert default_scale_epoch("Medisana BS 440 connect") == "1970"
    assert default_scale_epoch("Medisana BS 444 connect") == "2010"


def test_scale_rejects_unrelated_or_incomplete_packets():
    decoder = ScaleDecoder("2010")
    for uuid, packet in [
        (SCALE_WEIGHT_UUID, scale_weight()),
        (SCALE_BODY_UUID, scale_body()),
    ]:
        for length in range(len(packet)):
            with pytest.raises(ValueError):
                decoder.feed(uuid, packet[:length])
        with pytest.raises(ValueError):
            decoder.feed(uuid, b"\xff" + packet[1:])
    with pytest.raises(ValueError):
        decoder.feed(SCALE_WEIGHT_UUID, scale_weight(user=9))
