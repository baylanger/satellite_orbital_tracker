"""Sensor platform for Satellite Orbital Tracker."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = [
        ISSStatusSensor(coordinator, entry),
        ISSAltitudeSensor(coordinator, entry),
        ISSMaxPassDegreesSensor(coordinator, entry),
    ]
    async_add_entities(sensors)

class ISSBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for satellite sensors."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:satellite-variant"

class ISSStatusSensor(ISSBaseSensor):
    """Representation of the main mission status."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.data['satellite_name']} Status"
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("status", "In Orbit")
        return "Initializing"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        return {
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "next_pass_time": data.get("next_pass_time"),
            "next_pass_event": data.get("next_pass_event"),
            "source": f"Local SGP4 Engine ({self.entry.data['satellite_name']})",
        }

class ISSAltitudeSensor(ISSBaseSensor):
    """Representation of the physical orbital altitude."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.data['satellite_name']} Orbital Altitude"
        self._attr_unique_id = f"{entry.entry_id}_altitude"
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("altitude_km")
        return None

class ISSMaxPassDegreesSensor(ISSBaseSensor):
    """Representation of the next pass peak sky elevation angle."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = f"{entry.data['satellite_name']} Next Pass Max Elevation"
        self._attr_unique_id = f"{entry.entry_id}_max_degrees"
        self._attr_native_unit_of_measurement = DEGREE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("next_pass_max_degrees")
        return None
