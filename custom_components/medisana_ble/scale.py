"""Classic Medisana 78B2 scale protocol.

Protocol and decoding adapted from keptenkurk/BS440 (MIT).
The original copyright and permission notice ships in THIRD_PARTY_NOTICES.txt.
No VitaDock implementation code or body-composition algorithms are included.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from struct import unpack_from

from .const import SCALE_BODY_UUID, SCALE_USER_UUID, SCALE_WEIGHT_UUID

SCALE_EPOCHS = {"1970": 0, "2010": 1262304000}


def default_scale_epoch(name: str) -> str:
    """Choose the clock variant documented for BS 410/430/440/444."""
    compact = name.upper().replace(" ", "").replace("-", "")
    return "1970" if any(model in compact for model in ("BS430", "BS440")) else "2010"


@dataclass(frozen=True, slots=True)
class ScaleMeasurement:
    """Weight and composition for one timestamp and one scale user."""

    weight: float
    user_id: int
    timestamp: datetime
    body_fat: float | None = None
    body_water: float | None = None
    muscle: float | None = None
    bone_mass: float | None = None
    basal_metabolism: int | None = None
    bmi: float | None = None


def scale_time_command(now: datetime, epoch: str) -> bytes:
    """Set the scale's RTC and request its stored readings (no deletion)."""
    seconds = int(now.timestamp()) - SCALE_EPOCHS[epoch]
    return b"\x02" + seconds.to_bytes(4, "little")


class ScaleDecoder:
    """Merge interleaved weight/body packets without mixing users or dates."""

    def __init__(self, epoch: str) -> None:
        self.epoch = SCALE_EPOCHS[epoch]
        self._weights: OrderedDict[tuple[int, int], ScaleMeasurement] = OrderedDict()
        self._bodies: OrderedDict[tuple[int, int], dict] = OrderedDict()
        self._heights: dict[int, int] = {}

    @staticmethod
    def _remember(cache: OrderedDict, key: tuple[int, int], value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > 512:
            cache.popitem(last=False)

    def feed(self, uuid: str, data: bytes) -> ScaleMeasurement | None:
        """Accept only the documented classic packet markers and field lengths."""
        if uuid == SCALE_USER_UUID:
            if not 9 <= len(data) <= 20 or data[0] != 0x84 or not 1 <= data[2] <= 8:
                raise ValueError("Invalid scale user packet")
            self._heights[data[2]] = data[6]
            matching = [
                (key, result)
                for key, result in self._weights.items()
                if result.user_id == data[2]
            ]
            if matching:
                key, result = max(matching, key=lambda item: item[0][0])
                height = data[6]
                result = replace(
                    result,
                    bmi=(
                        round(result.weight / (height / 100) ** 2, 1)
                        if height
                        else None
                    ),
                )
                self._remember(self._weights, key, result)
                return result
            return None
        if uuid == SCALE_WEIGHT_UUID:
            if not 14 <= len(data) <= 20 or data[0] != 0x1D:
                raise ValueError("Unsupported scale weight packet")
            weight = int.from_bytes(data[1:3], "little") / 100
            seconds = int.from_bytes(data[5:9], "little")
            user = data[13]
            if not 0 < weight <= 400 or not 0 <= user <= 8:
                raise ValueError("Invalid scale weight or user")
            timestamp = datetime.fromtimestamp(seconds + self.epoch, UTC)
            result = ScaleMeasurement(weight=weight, user_id=user, timestamp=timestamp)
            if height := self._heights.get(user):
                result = replace(result, bmi=round(weight / (height / 100) ** 2, 1))
            key = (seconds, user)
            if body := self._bodies.get(key):
                result = replace(result, **body)
            self._remember(self._weights, key, result)
            return result
        if uuid == SCALE_BODY_UUID:
            if not 16 <= len(data) <= 20 or data[0] != 0x6F:
                raise ValueError("Unsupported scale body packet")
            seconds, user, kcal, fat, water, muscle, bone = unpack_from(
                "<IBHHHHH", data, 1
            )
            if not 0 <= user <= 8:
                raise ValueError("Invalid scale body user")

            def percentage(raw: int) -> float | None:
                value = (raw & 0x0FFF) / 10
                return value if 0 <= value <= 100 else None

            body = {
                "basal_metabolism": kcal if kcal != 0xFFFF else None,
                "body_fat": percentage(fat),
                "body_water": percentage(water),
                "muscle": percentage(muscle),
                "bone_mass": (bone & 0x0FFF) / 10 if bone != 0xFFFF else None,
            }
            key = (seconds, user)
            self._remember(self._bodies, key, body)
            if weight := self._weights.get(key):
                result = replace(weight, **body)
                self._remember(self._weights, key, result)
                return result
            return None
        raise ValueError("Unsupported scale characteristic")
