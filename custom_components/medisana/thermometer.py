"""Decode Bluetooth SIG Health Thermometer measurements without HA dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .parser import _decode_timestamp

_FLOAT_SPECIAL_VALUES = frozenset(
    (0x007FFFFE, 0x007FFFFF, 0x00800000, 0x00800001, 0x00800002)
)


@dataclass(frozen=True, slots=True)
class TemperatureMeasurement:
    """One temperature measurement, with optional Bluetooth SIG fields."""

    temperature: float | None
    unit: str
    timestamp: datetime | None = None
    temperature_type: int | None = None


def _decode_float(data: bytes) -> float | None:
    """Decode IEEE 11073 FLOAT (signed 24-bit mantissa, 8-bit exponent)."""
    if int.from_bytes(data, "little") in _FLOAT_SPECIAL_VALUES:
        return None
    mantissa = int.from_bytes(data[:3], "little", signed=True)
    exponent = int.from_bytes(data[3:4], "little", signed=True)
    return float(f"{mantissa}e{exponent}")


def parse_temperature_measurement(data: bytes) -> TemperatureMeasurement:
    """Parse 0x2A1C, rejecting incomplete or unsupported packet layouts.

    Special numeric values and unknown device timestamps become None. Bluetooth
    Date Time has no timezone; Home Assistant applies its configured timezone.
    """
    if not data:
        raise ValueError("Empty temperature measurement")
    flags = data[0]
    if flags & 0xF8:
        raise ValueError("Unsupported temperature measurement flags")
    expected_length = 5 + (7 if flags & 0x02 else 0) + (1 if flags & 0x04 else 0)
    if len(data) != expected_length:
        raise ValueError(
            f"Temperature measurement requires {expected_length} bytes; "
            f"received {len(data)}"
        )

    offset = 5
    timestamp = None
    if flags & 0x02:
        timestamp = _decode_timestamp(data[offset : offset + 7])
        offset += 7
    return TemperatureMeasurement(
        temperature=_decode_float(data[1:5]),
        unit="°F" if flags & 0x01 else "°C",
        timestamp=timestamp,
        temperature_type=data[offset] if flags & 0x04 else None,
    )
