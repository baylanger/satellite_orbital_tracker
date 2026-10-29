"""The Satellite Orbital Tracker integration setup."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from skyfield.api import load

from .const import DOMAIN, normalize_norad_id
from .coordinator import SatelliteDataUpdateCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Satellite Orbital Tracker from a config entry in Home Assistant."""
    # Ensure the domain container dictionary exists within hass.data
    hass.data.setdefault(DOMAIN, {})

    # Initialize the data update coordinator for this specific satellite config entry
    # Skyfield reads its bundled time data files from disk when building the timescale, so do it in the executor
    ts = await hass.async_add_executor_job(load.timescale)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, ts)

    # Perform the initial data fetch so sensor entities have valid state immediately upon load
    await coordinator.async_config_entry_first_refresh()

    # Store the coordinator instance keyed by the unique entry ID
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup request to the sensor platforms
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "binary_sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up associated coordinator resources and platforms."""
    # Unload sensor platforms safely
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor"])

    # Remove the coordinator instance from hass.data if platform cleanup succeeded
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the saved TLE cache and any repair issue once a satellite is removed from Home Assistant."""
    # Entries created before NORAD IDs were normalized may have saved their cache under the raw ID
    norad_ids = {str(entry.data["norad_id"]), normalize_norad_id(entry.data["norad_id"]) or str(entry.data["norad_id"])}
    for norad_id in norad_ids:
        await Store(hass, 1, f"{DOMAIN}_{norad_id}").async_remove()
        ir.async_delete_issue(hass, DOMAIN, f"satellite_lost_{norad_id}")
