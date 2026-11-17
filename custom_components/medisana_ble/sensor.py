"""Sensors for the latest Medisana measurement."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfMass,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DEVICE_TYPE_BLOOD_PRESSURE,
    DEVICE_TYPE_GLUCOSE,
    DEVICE_TYPE_PULSE_OXIMETER,
    DEVICE_TYPE_SCALE,
    DEVICE_TYPE_THERMOMETER,
    DOMAIN,
    USER_FILTER_DEVICE_TYPES,
)
from .coordinator import MedisanaCoordinator
from .glucose import GlucoseMeasurement
from .parser import BloodPressureMeasurement
from .pulse_oximeter import PulseOximeterMeasurement
from .scale import ScaleMeasurement
from .thermometer import TemperatureMeasurement

PARALLEL_UPDATES = 0

# Statistics summarize the retained values over time; device timestamps remain
# available separately for interpreting individual measurements.
BLOOD_PRESSURE_SENSORS = (
    SensorEntityDescription(
        key="systolic",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="systolic",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="diastolic",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="diastolic",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="mean_arterial_pressure",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="mean_arterial_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="pulse",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="pulse",
        native_unit_of_measurement="bpm",
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="user_id",
        translation_key="user_id",
        icon="mdi:account",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
    ),
)
THERMOMETER_SENSORS = (
    SensorEntityDescription(
        key="temperature",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
    ),
)
SCALE_SENSORS = (
    SensorEntityDescription(
        key="weight",
        translation_key="weight",
        icon="mdi:scale-bathroom",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            icon="mdi:human",
            native_unit_of_measurement="%",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        )
        for key in ("body_fat", "body_water", "muscle")
    ),
    SensorEntityDescription(
        key="bone_mass",
        translation_key="bone_mass",
        icon="mdi:bone",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="bmi",
        translation_key="bmi",
        icon="mdi:human",
        native_unit_of_measurement="kg/m²",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="basal_metabolism",
        translation_key="basal_metabolism",
        icon="mdi:fire",
        native_unit_of_measurement="kcal/day",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    BLOOD_PRESSURE_SENSORS[-1],
)
PULSE_OXIMETER_SENSORS = (
    SensorEntityDescription(
        key="oxygen_saturation",
        translation_key="oxygen_saturation",
        icon="mdi:water-percent",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    BLOOD_PRESSURE_SENSORS[3],
    SensorEntityDescription(
        key="pulse_amplitude_index",
        translation_key="pulse_amplitude_index",
        icon="mdi:pulse",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
)
GLUCOSE_SENSORS = (
    SensorEntityDescription(
        key="glucose",
        translation_key="glucose",
        icon="mdi:diabetes",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement="mg/dL",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
)
DEVICE_SENSORS = {
    DEVICE_TYPE_BLOOD_PRESSURE: BLOOD_PRESSURE_SENSORS,
    DEVICE_TYPE_THERMOMETER: THERMOMETER_SENSORS,
    DEVICE_TYPE_SCALE: SCALE_SENSORS,
    DEVICE_TYPE_PULSE_OXIMETER: PULSE_OXIMETER_SENSORS,
    DEVICE_TYPE_GLUCOSE: GLUCOSE_SENSORS,
}
COMMON_SENSORS = (
    SensorEntityDescription(
        key="battery_level",
        state_class=SensorStateClass.MEASUREMENT,
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        icon="mdi:battery",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)
TIMESTAMP_SENSORS = (
    SensorEntityDescription(
        key="last_measurement",
        translation_key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="last_received",
        translation_key="last_received",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

_RESTORED_ATTRIBUTES = (
    "configured_user_id",
    "user_id",
    "raw_user_id",
    "measurement_time",
    "measurement_status",
    "temperature_type",
    "sequence_number",
    "sensor_status",
    "type_sample_location",
    "meal",
    "device_sensor_status",
    "measurement_type",
    "manufacturer_name",
    "model_number",
    "firmware_revision",
    "serial_number",
    "hardware_revision",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create only the sensors supported by the configured device type."""
    coordinator: MedisanaCoordinator = entry.runtime_data
    descriptions = DEVICE_SENSORS[coordinator.device_type]
    async_add_entities(
        MedisanaSensor(coordinator, description)
        for description in (*descriptions, *COMMON_SENSORS, *TIMESTAMP_SENSORS)
    )
    if coordinator.separate_users:
        added_users: set[int] = set()
        # Restore existing user groups even if retention is disabled or their
        # last record has fallen outside the bounded history window.
        prefix = re.escape(coordinator.address.lower())
        registered_users = {
            int(match[1])
            for entity in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
            if (
                match := re.fullmatch(prefix + r"_user_(\d+)_[a-z_]+", entity.unique_id)
            )
        }

        @callback
        def add_users() -> None:
            new_users = (
                set(coordinator.user_measurements) | registered_users
            ) - added_users
            added_users.update(new_users)
            async_add_entities(
                MedisanaSensor(coordinator, description, user_id=user)
                for user in sorted(new_users)
                for description in (*descriptions, *TIMESTAMP_SENSORS)
            )

        add_users()
        entry.async_on_unload(coordinator.async_add_listener(add_users))


class MedisanaSensor(CoordinatorEntity[MedisanaCoordinator], RestoreSensor):
    """Keep a measurement readable when the battery-powered device sleeps."""

    _attr_has_entity_name = True
    _attr_native_value = None

    def __init__(
        self,
        coordinator: MedisanaCoordinator,
        description: SensorEntityDescription,
        user_id: int | None = None,
    ) -> None:
        """Initialize a sensor with a stable, consistent entity name."""
        super().__init__(coordinator)
        self.entity_description = description
        self._user_id = user_id
        prefix = coordinator.address.lower()
        if user_id is not None:
            prefix += f"_user_{user_id}"
        self._attr_unique_id = f"{prefix}_{description.key}"
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        is_thermometer = coordinator.device_type == DEVICE_TYPE_THERMOMETER
        self._force_update_on_measurement = is_thermometer and description.key in {
            "temperature",
            "last_measurement",
        }
        self._last_measurement_sequence = coordinator.measurement_sequence
        self._attr_extra_state_attributes = (
            {"configured_user_id": self._configured_user_id}
            if coordinator.device_type in USER_FILTER_DEVICE_TYPES
            else {}
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.name,
            manufacturer="Medisana",
            model={
                DEVICE_TYPE_THERMOMETER: "TM 750 connect",
                DEVICE_TYPE_BLOOD_PRESSURE: "Bluetooth blood pressure monitor",
                DEVICE_TYPE_SCALE: "Classic Bluetooth body analysis scale",
                DEVICE_TYPE_PULSE_OXIMETER: "Bluetooth pulse oximeter",
                DEVICE_TYPE_GLUCOSE: "Bluetooth glucose meter",
            }[coordinator.device_type],
        )
        if user_id is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.address}_user_{user_id}")},
                name=f"{coordinator.name} ({user_id})",
                manufacturer="Medisana",
                model="Bluetooth user ID " + str(user_id),
                via_device=(DOMAIN, coordinator.address),
            )

    @property
    def _configured_user_id(self) -> int:
        return (
            self._user_id
            if self._user_id is not None
            else self.coordinator.user_id_filter
        )

    @property
    def _measurement(self):
        if self._user_id is not None:
            return self.coordinator.user_measurements.get(self._user_id)
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """A sleeping or disconnected device does not invalidate its last reading."""
        return True

    async def async_added_to_hass(self) -> None:
        """Restore native values and units without mixing configured users."""
        await super().async_added_to_hass()
        if self._measurement is not None:
            self._update_measurement()
            return

        last_state = await self.async_get_last_state()
        if last_state is None or (
            self.coordinator.device_type in USER_FILTER_DEVICE_TYPES
            and last_state.attributes.get("configured_user_id")
            != self._configured_user_id
        ):
            return
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None:
            if self._user_id is None:
                self.coordinator.restore_sensor_data(
                    self.entity_description.key,
                    last_data.native_value,
                    last_data.native_unit_of_measurement,
                    last_state.attributes,
                )
            else:
                self.coordinator.restore_user_sensor_data(
                    self._user_id,
                    self.entity_description.key,
                    last_data.native_value,
                    last_data.native_unit_of_measurement,
                    last_state.attributes,
                )
            self._attr_native_value = last_data.native_value
            self._attr_native_unit_of_measurement = last_data.native_unit_of_measurement
            self._attr_extra_state_attributes = {
                key: last_state.attributes[key]
                for key in _RESTORED_ATTRIBUTES
                if key in last_state.attributes
            }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Copy the accepted reading and update all its associated metadata."""
        measurement_sequence = self.coordinator.measurement_sequence
        self._attr_force_update = (
            self._force_update_on_measurement
            and measurement_sequence != self._last_measurement_sequence
        )
        self._update_measurement()
        self.async_write_ha_state()
        self._attr_force_update = False
        self._last_measurement_sequence = measurement_sequence

    @callback
    def _update_measurement(self) -> None:
        """Replace values as one reading, including missing optional fields."""
        measurement = self._measurement
        device_info = self.coordinator.device_info
        if measurement is None:
            # Optional battery/device details can arrive before the first
            # measurement. Preserve any restored reading while updating them.
            if (
                self.entity_description.key == "battery_level"
                and self.coordinator.battery_level is not None
            ):
                self._attr_native_value = self.coordinator.battery_level
            self._attr_extra_state_attributes = {
                **self._attr_extra_state_attributes,
                **device_info,
            }
            return
        timestamp = self._device_timestamp(measurement.timestamp)
        attributes: dict[str, Any] = {}
        if isinstance(measurement, BloodPressureMeasurement):
            attributes["configured_user_id"] = self._configured_user_id
            if measurement.user_id is not None:
                attributes["user_id"] = measurement.user_id
            if measurement.measurement_status is not None:
                attributes["measurement_status"] = measurement.measurement_status
            if self.entity_description.device_class == SensorDeviceClass.PRESSURE:
                self._attr_native_unit_of_measurement = measurement.pressure_unit
        elif isinstance(measurement, TemperatureMeasurement):
            if measurement.temperature_type is not None:
                attributes["temperature_type"] = measurement.temperature_type
            if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE:
                self._attr_native_unit_of_measurement = measurement.unit
        elif isinstance(measurement, ScaleMeasurement):
            attributes["configured_user_id"] = self._configured_user_id
            if measurement.user_id is not None:
                attributes["user_id"] = measurement.user_id
            if measurement.raw_user_id is not None:
                attributes["raw_user_id"] = measurement.raw_user_id
        elif isinstance(measurement, GlucoseMeasurement):
            self._attr_native_unit_of_measurement = (
                measurement.unit
                if self.entity_description.key == "glucose"
                else self.entity_description.native_unit_of_measurement
            )
            for name in (
                "sequence_number",
                "sensor_status",
                "type_sample_location",
                "meal",
            ):
                if (value := getattr(measurement, name)) is not None:
                    attributes[name] = value
        elif isinstance(measurement, PulseOximeterMeasurement):
            attributes["measurement_type"] = measurement.measurement_type
            for name in ("measurement_status", "device_sensor_status"):
                if (value := getattr(measurement, name)) is not None:
                    attributes[name] = value
        if timestamp is not None:
            attributes["measurement_time"] = timestamp.isoformat()
        if device_info:
            attributes.update(device_info)

        key = self.entity_description.key
        if key == "last_measurement":
            self._attr_native_value = timestamp
        elif key == "last_received":
            self._attr_native_value = (
                self.coordinator.user_received_at.get(self._user_id)
                if self._user_id is not None
                else self.coordinator.received_at
            )
        elif key == "battery_level":
            self._attr_native_value = getattr(self.coordinator, "battery_level", None)
        else:
            self._attr_native_value = getattr(measurement, key, None)
        self._attr_extra_state_attributes = attributes

    def _device_timestamp(self, timestamp: datetime | None) -> datetime | None:
        """Interpret Bluetooth Date Time in the Home Assistant time zone."""
        if timestamp is None or timestamp.tzinfo is not None:
            return timestamp
        return timestamp.replace(tzinfo=dt_util.get_default_time_zone())
