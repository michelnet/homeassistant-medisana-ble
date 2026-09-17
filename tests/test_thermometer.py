"""Thermometer protocol regressions, runnable without Home Assistant installed."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

# A private package loads both pure parsers without executing the integration.
_PACKAGE_NAME = "medisana_ble_thermometer_protocol_under_test"
_COMPONENT_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "medisana_ble"
)
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_COMPONENT_PATH)]
sys.modules[_PACKAGE_NAME] = _PACKAGE
_SPEC = importlib.util.spec_from_file_location(
    f"{_PACKAGE_NAME}.thermometer", _COMPONENT_PATH / "thermometer.py"
)
assert _SPEC is not None and _SPEC.loader is not None
thermometer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = thermometer
_SPEC.loader.exec_module(thermometer)

_DATE_BYTES = bytes.fromhex("e8 07 02 1d 17 3b 3a")  # 2024-02-29 23:59:58
_DATE = datetime(2024, 2, 29, 23, 59, 58)
_TEMPERATURE = bytes.fromhex("6d 01 00 ff")  # 365 * 10^-1 = 36.5


def temperature_packet(flags: int) -> bytes:
    """Build all layouts independently from the decoder's field offsets."""
    packet = bytes([flags]) + _TEMPERATURE
    if flags & 0x02:
        packet += _DATE_BYTES
    if flags & 0x04:
        packet += bytes([3])
    return packet


class TemperatureParserTests(unittest.TestCase):
    def test_all_flag_combinations(self) -> None:
        """Both units and all optional fields preserve their intended offsets."""
        for flags in range(8):
            with self.subTest(flags=flags):
                reading = thermometer.parse_temperature_measurement(
                    temperature_packet(flags)
                )
                self.assertEqual(reading.temperature, 36.5)
                self.assertEqual(reading.unit, "°F" if flags & 1 else "°C")
                self.assertEqual(reading.timestamp, _DATE if flags & 2 else None)
                self.assertEqual(reading.temperature_type, 3 if flags & 4 else None)

    def test_real_ts42b_measurement(self) -> None:
        """Retain the original captured Medisana TM 750 connect regression."""
        reading = thermometer.parse_temperature_measurement(
            bytes.fromhex("06 5f 01 00 ff dc 07 07 1b 0f 0c 00 03")
        )
        self.assertEqual(reading.temperature, 35.1)
        self.assertEqual(reading.unit, "°C")
        self.assertEqual(reading.timestamp, datetime(2012, 7, 27, 15, 12))
        self.assertEqual(reading.temperature_type, 3)

    def test_fahrenheit_preserves_value(self) -> None:
        reading = thermometer.parse_temperature_measurement(
            bytes.fromhex("01 da 03 00 ff")
        )
        self.assertEqual(reading.temperature, 98.6)
        self.assertEqual(reading.unit, "°F")

    def test_truncation_of_every_layout(self) -> None:
        for flags in range(8):
            packet = temperature_packet(flags)
            for size in range(len(packet)):
                with (
                    self.subTest(flags=flags, size=size),
                    self.assertRaises(ValueError),
                ):
                    thermometer.parse_temperature_measurement(packet[:size])

    def test_unexpected_bytes(self) -> None:
        for flags in range(8):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                thermometer.parse_temperature_measurement(
                    temperature_packet(flags) + b"\x00"
                )

    def test_reserved_flags(self) -> None:
        for flags in range(8, 256):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                thermometer.parse_temperature_measurement(temperature_packet(flags))

    def test_signed_mantissa_and_decimal_exponents(self) -> None:
        for raw, expected in (
            ("2e fb ff ff", -123.4),
            ("78 00 00 01", 1200.0),
            ("ff ff ff fb", -0.00001),
            ("01 00 00 80", 1e-128),
            ("01 00 00 7f", 1e127),
        ):
            with self.subTest(raw=raw):
                reading = thermometer.parse_temperature_measurement(
                    bytes([0]) + bytes.fromhex(raw)
                )
                self.assertEqual(reading.temperature, expected)

    def test_special_float_values_preserve_optional_fields(self) -> None:
        for raw in (0x007FFFFE, 0x007FFFFF, 0x00800000, 0x00800001, 0x00800002):
            with self.subTest(raw=hex(raw)):
                packet = (
                    bytes([0x06]) + raw.to_bytes(4, "little") + _DATE_BYTES + b"\x03"
                )
                reading = thermometer.parse_temperature_measurement(packet)
                self.assertIsNone(reading.temperature)
                self.assertEqual(reading.timestamp, _DATE)
                self.assertEqual(reading.temperature_type, 3)

    def test_special_encodings_require_zero_exponent(self) -> None:
        for raw, expected in (
            ("ff ff 7f ff", 838860.7),
            ("00 00 80 ff", -838860.8),
        ):
            with self.subTest(raw=raw):
                reading = thermometer.parse_temperature_measurement(
                    bytes([0]) + bytes.fromhex(raw)
                )
                self.assertEqual(reading.temperature, expected)

    def test_zero_temperature_is_a_measurement(self) -> None:
        reading = thermometer.parse_temperature_measurement(bytes(5))
        self.assertEqual(reading.temperature, 0.0)


class TemperatureTimestampTests(unittest.TestCase):
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
                packet = bytes([0x06]) + _TEMPERATURE + bytes.fromhex(date) + b"\x03"
                reading = thermometer.parse_temperature_measurement(packet)
                self.assertIsNone(reading.timestamp)
                self.assertEqual(reading.temperature, 36.5)
                self.assertEqual(reading.temperature_type, 3)

    def test_timestamp_remains_naive(self) -> None:
        reading = thermometer.parse_temperature_measurement(temperature_packet(0x02))
        self.assertIsNone(reading.timestamp.tzinfo)


if __name__ == "__main__":
    unittest.main()
