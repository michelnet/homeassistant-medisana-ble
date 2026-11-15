"""Decode standard Bluetooth Glucose measurements and matching context."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .parser import _decode_sfloat, _decode_timestamp


@dataclass(frozen=True, slots=True)
class GlucoseMeasurement:
    """Glucose concentration in display units, with device record identity."""

    glucose: float | None
    unit: str
    sequence_number: int
    timestamp: datetime | None
    time_offset: int = 0
    sensor_status: int | None = None
    type_sample_location: int | None = None
    context_follows: bool = False
    meal: int | None = None


def parse_glucose(data: bytes, *, time_offset: int = 0) -> GlucoseMeasurement:
    """Decode 2A18, including carried time offset and kg/L or mol/L units."""
    if not data or data[0] & 0xE0:
        raise ValueError("Unsupported glucose measurement flags")
    flags = data[0]
    expected = 10 + (2 if flags & 1 else 0) + (3 if flags & 2 else 0)
    expected += 2 if flags & 8 else 0
    if len(data) != expected:
        raise ValueError("Incomplete or oversized glucose measurement")
    timestamp = _decode_timestamp(data[3:10])
    offset = 10
    if flags & 1:
        time_offset = int.from_bytes(data[offset : offset + 2], "little", signed=True)
        offset += 2
    if timestamp is not None:
        try:
            timestamp += timedelta(minutes=time_offset)
        except OverflowError:
            timestamp = None
    concentration = None
    sample = None
    if flags & 2:
        concentration = _decode_sfloat(data[offset : offset + 2])
        if concentration is not None:
            concentration = round(concentration * (1000 if flags & 4 else 100000), 8)
            if concentration < 0:
                concentration = None
        sample = data[offset + 2]
        # The low nibble is specimen type, the high nibble sample location.
        # Control-solution checks are not patient glucose measurements.
        if sample & 0x0F == 0x0A or sample >> 4 == 4:
            concentration = None
        offset += 3
    status = int.from_bytes(data[offset : offset + 2], "little") if flags & 8 else None
    # Faulted measurements must not become normal glucose readings. Bit 0 is
    # a low-battery warning; bit 11 invalidates only the device timestamp.
    if status is not None and status & 0x07FE:
        concentration = None
    if status is not None and status & 0x0800:
        timestamp = None
    return GlucoseMeasurement(
        glucose=concentration,
        unit="mmol/L" if flags & 4 else "mg/dL",
        sequence_number=int.from_bytes(data[1:3], "little"),
        timestamp=timestamp,
        time_offset=time_offset,
        sensor_status=status,
        type_sample_location=sample,
        context_follows=bool(flags & 16),
    )


def parse_glucose_context(data: bytes) -> tuple[int, int | None]:
    """Validate all 2A34 fields; retain the meal marker for the matching record."""
    if len(data) < 3:
        raise ValueError("Incomplete glucose context")
    flags = data[0]
    expected = 3 + sum(
        length
        for bit, length in ((1, 3), (2, 1), (4, 1), (8, 3), (16, 3), (64, 2), (128, 1))
        if flags & bit
    )
    if len(data) != expected or not flags & 0xDF:
        raise ValueError("Invalid glucose context layout")
    offset = 3
    if flags & 128:
        if data[offset]:
            raise ValueError("Unsupported extended glucose context flags")
        offset += 1
    if flags & 1:
        offset += 3
    meal = data[offset] if flags & 2 else None
    return int.from_bytes(data[1:3], "little"), meal


class GlucoseDecoder:
    """Join context by sequence number, with bounded per-connection state."""

    def __init__(self) -> None:
        self.time_offset = 0
        self._pending: OrderedDict[int, GlucoseMeasurement] = OrderedDict()
        self._contexts: OrderedDict[int, int | None] = OrderedDict()

    def measurement(self, data: bytes) -> GlucoseMeasurement | None:
        result = parse_glucose(data, time_offset=self.time_offset)
        self.time_offset = result.time_offset
        if result.context_follows:
            if result.sequence_number in self._contexts:
                return replace(result, meal=self._contexts.pop(result.sequence_number))
            self._pending[result.sequence_number] = result
            if len(self._pending) > 128:
                self._pending.popitem(last=False)
            return None
        return result

    def context(self, data: bytes) -> GlucoseMeasurement | None:
        sequence, meal = parse_glucose_context(data)
        if result := self._pending.pop(sequence, None):
            return replace(result, meal=meal)
        self._contexts[sequence] = meal
        if len(self._contexts) > 128:
            self._contexts.popitem(last=False)
        return None
