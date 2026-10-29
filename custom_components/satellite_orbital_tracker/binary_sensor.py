"""Binary sensor platform for Satellite Orbital Tracker."""
from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SatelliteBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors of the tracked satellite."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SatelliteSunlitSensor(coordinator, entry)])


class SatelliteSunlitSensor(SatelliteBaseEntity, BinarySensorEntity):
    """On while the Sun shines on the satellite, off while it is in the Earth's shadow."""

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_name = "Sunlit"
        self._attr_unique_id = f"{entry.entry_id}_sunlit"
        self._attr_device_class = BinarySensorDeviceClass.LIGHT
        self._attr_entity_registry_enabled_default = False  # enable it in the UI if wanted

    @property
    @override
    def is_on(self) -> bool | None:
        """Return whether the satellite is sunlit, or None if unknown."""
        if self.coordinator.data:
            return self.coordinator.data.get("sunlit")
        return None
