"""Sensors for the latest Medisana measurement."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_THERMOMETER, DOMAIN
from .coordinator import MedisanaCoordinator
from .parser import BloodPressureMeasurement
from .thermometer import TemperatureMeasurement

PARALLEL_UPDATES = 0

# These are the latest individual readings, potentially uploaded from device
# memory. They are not continuous present-time measurements, so they do not opt
# into time-weighted long-term statistics with a measurement state class.
BLOOD_PRESSURE_SENSORS = (
    SensorEntityDescription(
        key="systolic",
        translation_key="systolic",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="diastolic",
        translation_key="diastolic",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="mean_arterial_pressure",
        translation_key="mean_arterial_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_unit_of_measurement=UnitOfPressure.MMHG,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
    ),
    SensorEntityDescription(
        key="pulse",
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
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
    ),
)
COMMON_SENSORS = (
    SensorEntityDescription(
        key="battery_level",
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
    "measurement_time",
    "measurement_status",
    "temperature_type",
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
    descriptions = (
        THERMOMETER_SENSORS
        if coordinator.device_type == DEVICE_TYPE_THERMOMETER
        else BLOOD_PRESSURE_SENSORS
    )
    async_add_entities(
        MedisanaSensor(coordinator, description)
        for description in (*descriptions, *COMMON_SENSORS, *TIMESTAMP_SENSORS)
    )


class MedisanaSensor(CoordinatorEntity[MedisanaCoordinator], RestoreSensor):
    """Keep a measurement readable when the battery-powered device sleeps."""

    _attr_has_entity_name = True
    _attr_native_value = None

    def __init__(
        self,
        coordinator: MedisanaCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a sensor with a stable, consistent entity name."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address.lower()}_{description.key}"
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        is_thermometer = coordinator.device_type == DEVICE_TYPE_THERMOMETER
        # A new thermometer reading can legitimately have the same value and
        # no device timestamp. Publish every accepted reading so Home Assistant
        # updates last_updated and state-based automations in that case too.
        self._attr_force_update = is_thermometer and description.key == "temperature"
        self._attr_extra_state_attributes = (
            {} if is_thermometer else {"configured_user_id": coordinator.user_id_filter}
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.name,
            manufacturer="Medisana",
            model=(
                "TM 750 connect"
                if is_thermometer
                else "Bluetooth blood pressure monitor"
            ),
        )

    @property
    def available(self) -> bool:
        """A sleeping or disconnected device does not invalidate its last reading."""
        return True

    async def async_added_to_hass(self) -> None:
        """Restore native values and units without mixing configured users."""
        await super().async_added_to_hass()
        if self.coordinator.data is not None:
            self._update_measurement()
            return

        last_state = await self.async_get_last_state()
        if last_state is None or (
            self.coordinator.device_type != DEVICE_TYPE_THERMOMETER
            and last_state.attributes.get("configured_user_id")
            != self.coordinator.user_id_filter
        ):
            return
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None:
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
        self._update_measurement()
        self.async_write_ha_state()

    @callback
    def _update_measurement(self) -> None:
        """Replace values as one reading, including missing optional fields."""
        measurement = self.coordinator.data
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
            attributes["configured_user_id"] = self.coordinator.user_id_filter
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
        if timestamp is not None:
            attributes["measurement_time"] = timestamp.isoformat()
        if device_info:
            attributes.update(device_info)

        key = self.entity_description.key
        if key == "last_measurement":
            self._attr_native_value = timestamp
        elif key == "last_received":
            self._attr_native_value = self.coordinator.received_at
        elif key == "battery_level":
            self._attr_native_value = getattr(self.coordinator, "battery_level", None)
        else:
            self._attr_native_value = getattr(measurement, key, None)
        self._attr_extra_state_attributes = attributes

    def _device_timestamp(self, timestamp: datetime | None) -> datetime | None:
        """Interpret Bluetooth Date Time in the Home Assistant time zone."""
        if timestamp is None or timestamp.tzinfo is not None:
            return timestamp
        return timestamp.replace(tzinfo=ZoneInfo(self.hass.config.time_zone))
