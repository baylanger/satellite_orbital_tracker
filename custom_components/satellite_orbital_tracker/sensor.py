"""Sensor platform for Satellite Orbital Tracker."""
from __future__ import annotations

from datetime import datetime
import math
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, UnitOfLength, UnitOfSpeed, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, compass_point

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
        SatelliteStatusSensor(coordinator, entry),
        SatelliteAltitudeSensor(coordinator, entry),
        SatelliteSpeedSensor(coordinator, entry),
        SatelliteMaxPassDegreesSensor(coordinator, entry),
        SatelliteNextPassTimeSensor(coordinator, entry),
        SatelliteNextPassRemainingSensor(coordinator, entry),
        SatellitePassDurationSensor(coordinator, entry),
        SatellitePassRemainingSensor(coordinator, entry),
        SatelliteNextVisiblePassSensor(coordinator, entry),
        SatelliteSunriseSensor(coordinator, entry),
        SatelliteSunsetSensor(coordinator, entry),
    ]
    async_add_entities(sensors)

class SatelliteBaseEntity(CoordinatorEntity):
    """Base class for all satellite tracker entities providing common device info and entity naming."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the base entity."""
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

class SatelliteBaseSensor(SatelliteBaseEntity, SensorEntity):
    """Base class for satellite tracker sensors."""

class SatelliteStatusSensor(SatelliteBaseSensor):
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
            "next_pass_event": data.get("next_pass_event"),
            "crew_count": data.get("crew_count", 0),
            "crew_members": data.get("crew_members", []),
            "tle_epoch": data.get("tle_epoch"),
            "tle_last_fetched": data.get("tle_last_fetched"),
            "source": f"Local SGP4 Engine ({self.entry.title})",
        }

class SatelliteAltitudeSensor(SatelliteBaseSensor):
    """Representation of the physical orbital altitude in kilometers."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the altitude sensor entity."""
        super().__init__(coordinator, entry)
        self._attr_name = "Orbital Altitude"
        self._attr_unique_id = f"{entry.entry_id}_altitude"
        self._attr_entity_registry_enabled_default = False  # new installs: enable it in the UI if wanted
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

class SatelliteSpeedSensor(SatelliteBaseSensor):
    """Representation of the orbital speed."""

    def __init__(self, coordinator, entry: ConfigEntry):
        """Initialize the orbital speed sensor entity."""
        super().__init__(coordinator, entry)
        self._attr_name = "Orbital Speed"
        self._attr_unique_id = f"{entry.entry_id}_speed"
        self._attr_entity_registry_enabled_default = False  # enable it in the UI if wanted
        self._attr_device_class = SensorDeviceClass.SPEED
        self._attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 0

    @property
    @override
    def native_value(self):
        """Return the speed in km/h, or None if it is unknown."""
        if self.coordinator.data:
            val = self.coordinator.data.get("speed_km_s")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return val * 3600
        return None

    @property
    @override
    def extra_state_attributes(self):
        """Return the same speed in km/s."""
        if self.coordinator.data:
            val = self.coordinator.data.get("speed_km_s")
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return {"speed_km_s": round(val, 3)}
        return {}

class SatelliteMaxPassDegreesSensor(SatelliteBaseSensor):
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

class SatelliteNextPassTimeSensor(SatelliteBaseSensor):
    """Representation of the next pass time."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Time"
        self._attr_unique_id = f"{entry.entry_id}_next_pass_time"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    @override
    def native_value(self):
        """Return the next pass start as a timezone-aware datetime (UTC), or None if no pass is known."""
        if self.coordinator.data:
            val = self.coordinator.data.get("next_pass_time")
            if val:
                return datetime.fromisoformat(val)
        return None

class SatelliteNextPassRemainingSensor(SatelliteBaseSensor):
    """Representation of the time remaining until the next pass."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Pass Time Remaining"
        self._attr_unique_id = f"{entry.entry_id}_next_pass_time_remaining"
        self._attr_device_class = SensorDeviceClass.DURATION
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

class SatellitePassDurationSensor(SatelliteBaseSensor):
    """Representation of the length of the pass in progress, or of the next pass if none is in progress."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Pass Duration"
        self._attr_unique_id = f"{entry.entry_id}_pass_duration"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    @override
    def native_value(self):
        """Return the pass length in seconds (rise above the minimum elevation until set), or None if unknown."""
        if not self.coordinator.data:
            return None
        duration = self.coordinator.data.get("pass_duration")
        return None if duration is None else round(duration)

    @property
    @override
    def extra_state_attributes(self):
        """Return when that pass starts and ends (ISO8601, UTC)."""
        data = self.coordinator.data
        if not data:
            return {}
        return {"pass_start": data.get("pass_start"), "pass_end": data.get("pass_end")}

class SatellitePassRemainingSensor(SatelliteBaseSensor):
    """Representation of the time left in the pass in progress."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Pass Time Remaining"
        self._attr_unique_id = f"{entry.entry_id}_pass_time_remaining"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    @override
    def native_value(self):
        """Return seconds until the satellite sets, 0 while no pass is in progress, None if the end is unknown."""
        if not self.coordinator.data:
            return None
        remaining = self.coordinator.data.get("pass_time_remaining")
        return None if remaining is None else round(remaining)


class SatelliteShadowTimeSensor(SatelliteBaseSensor):
    """Base class for the times the satellite leaves or enters the Earth's shadow. Disabled until enabled in the UI."""

    _data_key = ""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_registry_enabled_default = False

    @property
    @override
    def native_value(self):
        """Return the time as a timezone-aware datetime (UTC), or None if none was found in the next 24 hours."""
        if self.coordinator.data:
            val = self.coordinator.data.get(self._data_key)
            if val:
                return datetime.fromisoformat(val)
        return None

class SatelliteSunriseSensor(SatelliteShadowTimeSensor):
    """Representation of the next time the Sun starts to shine on the satellite."""

    _data_key = "next_sunrise"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Satellite Sunrise"
        self._attr_unique_id = f"{entry.entry_id}_next_sunrise"

class SatelliteSunsetSensor(SatelliteShadowTimeSensor):
    """Representation of the next time the satellite enters the Earth's shadow."""

    _data_key = "next_sunset"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Satellite Sunset"
        self._attr_unique_id = f"{entry.entry_id}_next_sunset"


class SatelliteNextVisiblePassSensor(SatelliteBaseSensor):
    """Representation of when the next pass can be seen: above the horizon, lit by the Sun, over a dark sky."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Next Visible Pass"
        self._attr_unique_id = f"{entry.entry_id}_next_visible_pass"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_registry_enabled_default = False  # enable it in the UI if wanted

    def _visible_pass(self) -> dict | None:
        if self.coordinator.data:
            visible_pass = self.coordinator.data.get("next_visible_pass")
            if visible_pass and visible_pass.get("start"):
                return visible_pass
        return None

    @property
    @override
    def native_value(self):
        """Return when the satellite becomes visible, or None if no visible pass was found in the next 7 days."""
        visible_pass = self._visible_pass()
        return datetime.fromisoformat(visible_pass["start"]) if visible_pass else None

    @property
    @override
    def extra_state_attributes(self):
        """Return the end, length, peak and directions of the visible part of the pass (times in UTC)."""
        visible_pass = self._visible_pass()
        if not visible_pass:
            return {}
        return {
            "visible_end": visible_pass["end"],
            "duration": round(visible_pass["duration"]),
            "max_elevation": visible_pass["max_elevation"],
            "max_elevation_time": visible_pass["max_elevation_time"],
            "start_azimuth": visible_pass["start_azimuth"],
            "start_direction": compass_point(visible_pass["start_azimuth"]),
            "end_azimuth": visible_pass["end_azimuth"],
            "end_direction": compass_point(visible_pass["end_azimuth"]),
            "pass_start": visible_pass["pass_start"],
            "pass_end": visible_pass["pass_end"],
        }
