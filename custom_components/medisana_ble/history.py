"""Bounded, private local records and CSV export; no raw BLE payloads."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

CSV_FIELDS = (
    "device_type",
    "timestamp",
    "received_at",
    "user_id",
    "raw_user_id",
    "systolic",
    "diastolic",
    "mean_arterial_pressure",
    "pressure_unit",
    "pulse",
    "temperature",
    "unit",
    "weight",
    "body_fat",
    "body_water",
    "muscle",
    "bone_mass",
    "bmi",
    "basal_metabolism",
    "oxygen_saturation",
    "pulse_amplitude_index",
    "glucose",
    "sequence_number",
    "measurement_status",
    "sensor_status",
    "device_sensor_status",
    "type_sample_location",
    "meal",
    "temperature_type",
    "measurement_type",
    "time_offset",
    "context_follows",
)


class MeasurementHistory:
    """Retain original measurement times, deduplicate memory syncs, merge scales."""

    def __init__(self, hass: HomeAssistant, entry_id: str, limit: int) -> None:
        self.hass = hass
        self.limit = limit
        self.records: dict[str, dict] = {}
        self._store = Store(
            hass, 1, f"{DOMAIN}.{entry_id}.history", private=True, atomic_writes=True
        )

    async def async_load(self) -> None:
        """Load records before Bluetooth subscriptions start."""
        if not self.limit:
            # Disabling retention also removes previously persisted records.
            await self._store.async_remove()
            return
        if data := await self._store.async_load():
            self.records = {record["id"]: record for record in data["records"]}
            self._trim()

    def _trim(self) -> None:
        # Device timestamps sort the retained window even during oldest-first
        # or newest-first memory downloads. Missing clocks use reception time.
        ordered = sorted(
            self.records.values(),
            key=lambda r: (
                datetime.fromisoformat(
                    r.get("timestamp") or r["received_at"]
                ).timestamp(),
                datetime.fromisoformat(r["received_at"]).timestamp(),
            ),
        )
        self.records = {r["id"]: r for r in ordered[-(self.limit or 200) :]}

    @callback
    def record(
        self, device_type: str, measurement, received_at: datetime
    ) -> dict | None:
        """Return a new/updated record, or None for a repeated stored reading."""
        values = asdict(measurement)
        timestamp = measurement.timestamp
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo(self.hass.config.time_zone))
        values["timestamp"] = timestamp.isoformat() if timestamp is not None else None
        values["device_type"] = device_type
        # Scale characteristics describe one reading in several packets.
        if device_type == "scale":
            identity = {
                key: values.get(key)
                for key in (
                    "device_type",
                    "timestamp",
                    "user_id",
                    "raw_user_id",
                    "weight",
                )
            }
        else:
            identity = values.copy()
        # A thermometer can repeat a valid reading at the same device time.
        # Untimestamped continuous values likewise represent separate samples.
        if device_type == "thermometer" or (
            timestamp is None and device_type != "glucose"
        ):
            identity["sample_id"] = uuid4().hex
        record_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        previous = self.records.get(record_id)
        if previous is not None:
            if device_type == "scale":
                values = {
                    key: value if value is not None else previous.get(key)
                    for key, value in values.items()
                }
            if all(previous.get(key) == value for key, value in values.items()):
                return None
        result = {
            **values,
            "id": record_id,
            "received_at": previous["received_at"]
            if previous
            else received_at.isoformat(),
        }
        self.records[record_id] = result
        self._trim()
        if self.limit:
            self._store.async_delay_save(self._data, 2)
        return {**result, "updated": previous is not None}

    @callback
    def _data(self) -> dict:
        return {"records": list(self.records.values())}

    async def async_flush(self) -> None:
        """Save pending changes at unload/shutdown too."""
        if self.limit:
            await self._store.async_save(self._data())

    @callback
    def csv(self, user_id: int | None = None) -> tuple[str, int]:
        """Return a stable CSV schema without creating a public web file."""
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        records = [
            r
            for r in self.records.values()
            if self.limit and (user_id is None or r.get("user_id") == user_id)
        ]
        writer.writerows(records)
        return output.getvalue(), len(records)
