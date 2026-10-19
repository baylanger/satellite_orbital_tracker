"""Sensor platform for Satellite Orbital Tracker."""
from __future__ import annotations

import math
from typing import override

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for the satellite tracker integration from a config entry."""
    # Retrieve the shared data update coordinator instance for this entry from hass.data
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Instantiate the sensor suite for the tracked satellite
    sensors = [
        ISSStatusSensor(coordinator, entry),
        ISSAltitudeSensor(coordinator, entry),
        ISSMaxPassDegreesSensor(coordinator, entry),
        ISSNextPassTimeSensor(coordinator, entry),
        ISSNextPassRemainingSensor(coordinator, entry),

    ]
    async_add_entities(sensors)

class ISSBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for satellite tracker sensors providing common device info and entity naming."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the base sensor entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:satellite-variant"

        # Configure device registry metadata so all platform sensors group cleanly under one device entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Satellite {entry.title}",
            "manufacturer": "CelesTrak GP Data",
            "model": f"NORAD ID: {entry.data['norad_id']}",
        }

class ISSStatusSensor(ISSBaseSensor):
    """Representation of the main mission tracking status and telemetry attributes."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the status sensor entity."""
        super().__init__(coordinator, entry)
        self._attr_name = "Status"
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    @override
    def native_value(self):
        """Return current status string from coordinator data or fallback during initialization."""
        if self.coordinator.data:
            return self.coordinator.data.get("status", "In Orbit")
        return "Initializing"

    @property
    @override
    def extra_state_attributes(self):
        """Return rich orbital subpoint, pass details, and crew manifest as extra attributes."""
        data = self.coordinator.data
        if not data:
            return {}

        lat = data.get("latitude")
        lon = data.get("longitude")

        # Filter out NaN float values to keep state attributes clean and valid
        valid_lat = lat if lat is not None and not (isinstance(lat, float) and math.isnan(lat)) else None
        valid_lon = lon if lon is not None and not (isinstance(lon, float) and math.isnan(lon)) else None

        return {
            "latitude": valid_lat,
            "longitude": valid_lon,
            # "next_pass_time": data.get("next_pass_time"),
            "next_pass_event": data.get("next_pass_event"),
            "crew_count": data.get("crew_count", 0),
            "crew_members": data.get("crew_members", []),
            "source": f"Local SGP4 Engine ({self.entry.title})",
        }

class ISSAltitudeSensor(ISSBaseSensor):
    """Representation of the physical orbital altitude in kilometers."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the altitude sensor entity."""
        super().__init__(coordinator, entry)
        self._attr_name = "Orbital Altitude"
        self._attr_unique_id = f"{entry.entry_id}_altitude"
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self):
        """Return validated numeric altitude value in kilometers, ignoring NaN states."""
        if self.coordinator.data:
            val = self.coordinator.data.get("altitude_km")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return val
        return None

class ISSMaxPassDegreesSensor(ISSBaseSensor):
    """Representation of the next pass peak sky elevation angle in degrees."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the maximum pass elevation angle sensor entity."""
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Max Elevation"
        self._attr_unique_id = f"{entry.entry_id}_max_degrees"
        self._attr_native_unit_of_measurement = DEGREE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    @override
    def native_value(self):
        """Return validated peak elevation angle in degrees, ignoring NaN states."""
        if self.coordinator.data:
            val = self.coordinator.data.get("next_pass_max_degrees")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return val
        return None

class ISSNextPassTimeSensor(ISSBaseSensor):
    """Representation of the next pass time."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Time"
        self._attr_unique_id = f"{entry.entry_id}_next_pass_time"

    @property
    @override
    def native_value(self):
        if self.coordinator.data:
           val = self.coordinator.data.get("next_pass_time")
           return val[:val.rfind(".")] + "UTC+00:00"

class ISSNextPassRemainingSensor(ISSBaseSensor):
    """Representation of the time remaining until the next pass."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Time Remaining"
        self._attr_unique_id = f"{entry.entry_id}_next_pass_time_remaining"
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS   
        self._attr_state_class = SensorStateClass.MEASUREMENT 

    @property
    @override
    def native_value(self):

        if not self.coordinator.data:
            return None
        time_to_pass = self.coordinator.data.get("next_pass_time_remaining")

        if time_to_pass is None:
            return None
        return round(time_to_pass)
