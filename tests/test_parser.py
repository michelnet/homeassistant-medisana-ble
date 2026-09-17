"""Protocol regression tests, runnable without a Home Assistant installation."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Loading this pure module directly avoids executing the HA integration package.
_SPEC = importlib.util.spec_from_file_location(
     "medisana_ble_parser_under_test",
    Path(
        __file__
    ).resolve().parents[1]
     / "custom_components"
     / "medisana_ble")
assert _SPEC is not None and _SPEC.loader is not None
parser = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = parser
_SPEC.loader.exec_module(parser)

_DATE_BYTES = bytes.fromhex("e8 07 02 1d 17 3b 3a")  # 2024-02-29 23:59:58
_DATE = datetime(2024, 2, 29, 23, 59, 58)
_PRESSURES = bytes.fromhex("78 00 50 00 5d 00")  # 120 / 80, MAP 93


def blood_pressure_packet(flags: int) -> bytes:
    """Build independent test packets with each optional field present as flagged."""
    packet = bytes([flags]) + _PRESSURES
    if flags & 0x02:
        packet += _DATE_BYTES
    if flags & 0x04:
        packet += bytes.fromhex("d5 f2")  # 725 * 10^-1 = 72.5 bpm
    if flags & 0x08:
        packet += bytes([2])
    if flags & 0x10:
        packet += bytes.fromhex("25 00")
    return packet


class BloodPressureParserTests(unittest.TestCase):
    def test_all_flag_combinations(self) -> None:
        """Both units and every optional-field combination preserve field offsets."""
        for flags in range(32):
            with self.subTest(flags=flags):
                reading = parser.parse_blood_pressure(blood_pressure_packet(flags))
                self.assertEqual(reading.systolic, 120.0)
                self.assertEqual(reading.diastolic, 80.0)
                self.assertEqual(reading.mean_arterial_pressure, 93.0)
                self.assertEqual(reading.pressure_unit, "kPa" if flags & 1 else "mmHg")
                self.assertEqual(reading.timestamp, _DATE if flags & 2 else None)
                self.assertEqual(reading.pulse, 72.5 if flags & 4 else None)
                self.assertEqual(reading.user_id, 2 if flags & 8 else None)
                self.assertEqual(
                    reading.measurement_status, 0x25 if flags & 16 else None
                )

    def test_truncation_of_every_layout(self) -> None:
        for flags in range(32):
            packet = blood_pressure_packet(flags)
            for size in range(len(packet)):
                with (
                    self.subTest(flags=flags, size=size),
                    self.assertRaises(ValueError),
                ):
                    parser.parse_blood_pressure(packet[:size])

    def test_unexpected_bytes(self) -> None:
        for flags in range(32):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                parser.parse_blood_pressure(blood_pressure_packet(flags) + b"\x00")

    def test_reserved_flags(self) -> None:
        for flag in (0x20, 0x40, 0x80):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                parser.parse_blood_pressure(bytes([flag]) + _PRESSURES)

    def test_signed_sfloat_and_decimal_exponents(self) -> None:
        # -123.4, +1200, and -0.00001; mantissa and exponent are both signed.
        packet = bytes.fromhex("00 2e fb 78 10 18 8c")
        reading = parser.parse_blood_pressure(packet)
        self.assertEqual(reading.systolic, -123.4)
        self.assertEqual(reading.diastolic, 1200.0)
        self.assertEqual(reading.mean_arterial_pressure, -0.00001)

    def test_kpa_preserves_fraction(self) -> None:
        reading = parser.parse_blood_pressure(bytes.fromhex("01 a0 f0 6b f0 7c f0"))
        self.assertEqual(reading.pressure_unit, "kPa")
        self.assertEqual(reading.systolic, 16.0)
        self.assertEqual(reading.diastolic, 10.7)
        self.assertEqual(reading.mean_arterial_pressure, 12.4)

    def test_special_sfloat_values_in_every_numeric_field(self) -> None:
        for raw in (0x07FE, 0x07FF, 0x0800, 0x0801, 0x0802):
            for offset, field in (
                (1, "systolic"),
                (3, "diastolic"),
                (5, "mean_arterial_pressure"),
                (14, "pulse"),
            ):
                packet = bytearray(blood_pressure_packet(0x1E))
                packet[offset : offset + 2] = raw.to_bytes(2, "little")
                with self.subTest(raw=hex(raw), field=field):
                    reading = parser.parse_blood_pressure(packet)
                    self.assertIsNone(getattr(reading, field))
                    self.assertEqual(reading.timestamp, _DATE)
                    self.assertEqual(reading.user_id, 2)
                    self.assertEqual(reading.measurement_status, 0x25)

    def test_special_encodings_require_zero_exponent(self) -> None:
        reading = parser.parse_blood_pressure(bytes.fromhex("00 ff f7 00 f8 78 00"))
        self.assertEqual(reading.systolic, 204.7)
        self.assertEqual(reading.diastolic, -204.8)

    def test_unknown_and_zero_user_id(self) -> None:
        for user_id, expected in ((0xFF, None), (0, 0)):
            with self.subTest(user_id=user_id):
                packet = bytes([0x08]) + _PRESSURES + bytes([user_id])
                self.assertEqual(parser.parse_blood_pressure(packet).user_id, expected)

    def test_status_preserves_all_bits(self) -> None:
        packet = bytes([0x10]) + _PRESSURES + bytes.fromhex("01 80")
        self.assertEqual(parser.parse_blood_pressure(packet).measurement_status, 0x8001)

    def test_zero_values_are_measurements(self) -> None:
        reading = parser.parse_blood_pressure(
            bytes.fromhex("04 00 00 00 00 00 00 00 00")
        )
        self.assertEqual(reading.systolic, 0.0)
        self.assertEqual(reading.diastolic, 0.0)
        self.assertEqual(reading.mean_arterial_pressure, 0.0)
        self.assertEqual(reading.pulse, 0.0)


class TimestampTests(unittest.TestCase):
    def test_unknown_or_invalid_timestamp_preserves_reading(self) -> None:
        for date in (
            "00 00 02 1d 17 3b 3a",  # Unknown year
            "01 00 01 01 00 00 00",  # Reserved year below 1582
            "e8 07 00 1d 17 3b 3a",  # Unknown month
            "e8 07 02 00 17 3b 3a",  # Unknown day
            "e7 07 02 1d 17 3b 3a",  # Non-leap year, February 29
            "e8 07 0d 1d 17 3b 3a",  # Month 13
            "e8 07 02 1d 18 3b 3a",  # Hour 24
            "e8 07 02 1d 17 3c 3a",  # Minute 60
            "e8 07 02 1d 17 3b 3c",  # Second 60
            "ff ff 02 1d 17 3b 3a",  # Year beyond datetime's range
        ):
            with self.subTest(date=date):
                bp = bytes([0x0E]) + _PRESSURES + bytes.fromhex(date + " 48 00 01")
                reading = parser.parse_blood_pressure(bp)
                self.assertIsNone(reading.timestamp)
                self.assertEqual(reading.systolic, 120.0)
                self.assertEqual(reading.pulse, 72.0)
                self.assertEqual(reading.user_id, 1)

    def test_timestamp_remains_naive(self) -> None:
        reading = parser.parse_blood_pressure(blood_pressure_packet(0x02))
        self.assertIsNone(reading.timestamp.tzinfo)


if __name__ == "__main__":
    unittest.main()
