"""Decode Bluetooth SIG blood pressure measurements.

This parser has no Home Assistant or Bluetooth dependencies. It accepts the
complete value of characteristic 0x2A35. Device date/time
values have no timezone information; the caller must apply its local timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_SFLOAT_SPECIAL_VALUES = frozenset((0x07FE, 0x07FF, 0x0800, 0x0801, 0x0802))


@dataclass(frozen=True, slots=True)
class BloodPressureMeasurement:
    """One complete blood pressure measurement, with optional SIG fields."""

    systolic: float | None
    diastolic: float | None
    mean_arterial_pressure: float | None
    pressure_unit: str
    pulse: float | None
    user_id: int | None
    timestamp: datetime | None
    measurement_status: int | None


def _decode_sfloat(data: bytes) -> float | None:
    """Decode IEEE 11073 SFLOAT (signed 12-bit mantissa, 4-bit exponent)."""
    raw = int.from_bytes(data, "little")
    if raw in _SFLOAT_SPECIAL_VALUES:
        return None
    mantissa = raw & 0x0FFF
    if mantissa & 0x0800:
        mantissa -= 0x1000
    exponent = raw >> 12
    if exponent & 0x08:
        exponent -= 0x10
    return float(f"{mantissa}e{exponent}")


def _decode_timestamp(data: bytes) -> datetime | None:
    """Keep the measurement when a device clock is unset or invalid."""
    year = int.from_bytes(data[:2], "little")
    if not 1582 <= year <= 9999:
        return None
    try:
        return datetime(year, *data[2:7])
    except ValueError:
        # Bluetooth Date Time uses zero for an unknown year, month, or day.
        return None


def parse_blood_pressure(data: bytes) -> BloodPressureMeasurement:
    """Parse 0x2A35, rejecting incomplete or unsupported packet layouts.

    IEEE special numeric values and unknown user IDs become None. Measurement
    status is retained as the raw 16-bit flags; it is not a completion indicator.
    """
    if not data:
        raise ValueError("Empty blood pressure measurement")
    flags = data[0]
    if flags & 0xE0:
        raise ValueError("Unsupported blood pressure measurement flags")
    expected_length = (
        7
        + (7 if flags & 0x02 else 0)
        + (2 if flags & 0x04 else 0)
        + (1 if flags & 0x08 else 0)
        + (2 if flags & 0x10 else 0)
    )
    if len(data) != expected_length:
        raise ValueError(
            f"Blood pressure measurement requires {expected_length} bytes; "
            f"received {len(data)}"
        )

    systolic = _decode_sfloat(data[1:3])
    diastolic = _decode_sfloat(data[3:5])
    mean_arterial_pressure = _decode_sfloat(data[5:7])
    offset = 7
    timestamp = None
    if flags & 0x02:
        timestamp = _decode_timestamp(data[offset : offset + 7])
        offset += 7
    pulse = None
    if flags & 0x04:
        pulse = _decode_sfloat(data[offset : offset + 2])
        offset += 2
    user_id = None
    if flags & 0x08:
        user_id = data[offset] if data[offset] != 0xFF else None
        offset += 1
    measurement_status = None
    if flags & 0x10:
        measurement_status = int.from_bytes(data[offset : offset + 2], "little")

    return BloodPressureMeasurement(
        systolic=systolic,
        diastolic=diastolic,
        mean_arterial_pressure=mean_arterial_pressure,
        pressure_unit="kPa" if flags & 0x01 else "mmHg",
        pulse=pulse,
        user_id=user_id,
        timestamp=timestamp,
        measurement_status=measurement_status,
    )
