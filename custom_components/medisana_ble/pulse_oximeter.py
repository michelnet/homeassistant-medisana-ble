"""Independent Bluetooth SIG PLX measurement decoder (2A5E and 2A5F)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .parser import _decode_sfloat, _decode_timestamp


@dataclass(frozen=True, slots=True)
class PulseOximeterMeasurement:
    """A spot-check or continuous reading, including quality indicators."""

    oxygen_saturation: float | None
    pulse: float | None
    timestamp: datetime | None = None
    measurement_status: int | None = None
    device_sensor_status: int | None = None
    pulse_amplitude_index: float | None = None
    measurement_type: str = "spot_check"


def parse_plx(data: bytes, *, continuous: bool = False) -> PulseOximeterMeasurement:
    """Validate the complete layout before decoding any optional fields."""
    if not data or data[0] & 0xE0:
        raise ValueError("Unsupported PLX measurement flags")
    flags = data[0]
    if continuous:
        expected = 5 + (4 if flags & 1 else 0) + (4 if flags & 2 else 0)
        expected += (2 if flags & 4 else 0) + (3 if flags & 8 else 0)
        expected += 2 if flags & 16 else 0
    else:
        expected = 5 + (7 if flags & 1 else 0) + (2 if flags & 2 else 0)
        expected += (3 if flags & 4 else 0) + (2 if flags & 8 else 0)
    if len(data) != expected:
        raise ValueError("Incomplete or oversized PLX measurement")

    offset = 5
    timestamp = None
    if continuous:
        # Normal, fast and slow are separate value pairs. Publish only Normal.
        offset += (4 if flags & 1 else 0) + (4 if flags & 2 else 0)
    elif flags & 1:
        if not flags & 16:  # device clock is not set
            timestamp = _decode_timestamp(data[offset : offset + 7])
        offset += 7
    status_flag, sensor_flag, amplitude_flag = (4, 8, 16) if continuous else (2, 4, 8)
    status = None
    sensor_status = None
    amplitude = None
    if flags & status_flag:
        status = int.from_bytes(data[offset : offset + 2], "little")
        offset += 2
    if flags & sensor_flag:
        sensor_status = int.from_bytes(data[offset : offset + 3], "little")
        offset += 3
    if flags & amplitude_flag:
        amplitude = _decode_sfloat(data[offset : offset + 2])
    saturation, pulse = _decode_sfloat(data[1:3]), _decode_sfloat(data[3:5])
    # Preserve the received status, while keeping unavailable or invalid values
    # out of numeric statistics. Demonstration/testing values are also excluded.
    if (status is not None and status & 0xFC00) or (
        sensor_status is not None and sensor_status & 0xF802
    ):
        saturation = pulse = amplitude = None
    if saturation is not None and not 0 <= saturation <= 100:
        saturation = None
    if pulse is not None and pulse < 0:
        pulse = None
    if amplitude is not None and amplitude < 0:
        amplitude = None
    return PulseOximeterMeasurement(
        oxygen_saturation=saturation,
        pulse=pulse,
        timestamp=timestamp,
        measurement_status=status,
        device_sensor_status=sensor_status,
        pulse_amplitude_index=amplitude,
        measurement_type="continuous" if continuous else "spot_check",
    )


def parse_legacy_oximeter(data: bytes) -> PulseOximeterMeasurement:
    """Read the older PM 150 CD04 value, independently of the SIG PLX layout.

    Protocol facts: Tanaskoska et al., AIIT 2019, pp. 55–57; field offsets
    independently cross-checked against VitaDock+ 2.9.42's native metadata.
    The remaining vendor fields have no verified interpretation and are ignored.
    """
    if not 5 <= len(data) <= 20:
        raise ValueError("Incomplete or oversized legacy oximeter measurement")
    saturation, pulse = data[3], data[4]
    return PulseOximeterMeasurement(
        oxygen_saturation=float(saturation) if 0 < saturation <= 100 else None,
        pulse=float(pulse) if 0 < pulse < 255 else None,
        measurement_type="legacy_cd04",
    )
