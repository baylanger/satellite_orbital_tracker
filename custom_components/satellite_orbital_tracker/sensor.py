"""Sensor platform for Satellite Orbital Tracker."""
from __future__ import annotations

import math
from typing import override

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

        # Prepending "Satellite " to the device name forces the slug to start with "satellite_"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Satellite {entry.title}",
            "manufacturer": "CelesTrak GP Data",
            "model": f"NORAD ID: {entry.data['norad_id']}",
        }

class ISSStatusSensor(ISSBaseSensor):
    """Representation of the main mission status."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Status"
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    @override
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("status", "In Orbit")
        return "Initializing"

    @property
    @override
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}

        lat = data.get("latitude")
        lon = data.get("longitude")

        # Clean up nan values for attributes
        valid_lat = lat if lat is not None and not (isinstance(lat, float) and math.isnan(lat)) else None
        valid_lon = lon if lon is not None and not (isinstance(lon, float) and math.isnan(lon)) else None

        return {
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "next_pass_time": data.get("next_pass_time"),
            "next_pass_event": data.get("next_pass_event"),
            "crew_count": data.get("crew_count", 0),
            "crew_members": data.get("crew_members", []),
            "source": f"Local SGP4 Engine ({self.entry.title})",
        }

class ISSAltitudeSensor(ISSBaseSensor):
    """Representation of the physical orbital altitude."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Orbital Altitude"
        self._attr_unique_id = f"{entry.entry_id}_altitude"
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self):
        if self.coordinator.data:
            val = self.coordinator.data.get("altitude_km")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return val
        return None

class ISSMaxPassDegreesSensor(ISSBaseSensor):
    """Representation of the next pass peak sky elevation angle."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Max Elevation"
        self._attr_unique_id = f"{entry.entry_id}_max_degrees"
        self._attr_native_unit_of_measurement = DEGREE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self):
        if self.coordinator.data:
            val = self.coordinator.data.get("next_pass_max_degrees")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return val
        return None
