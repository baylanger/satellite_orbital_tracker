"""Data update coordinator for Satellite Orbital Tracker."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
from typing import override
import aiohttp

from skyfield.api import EarthSatellite, load, wgs84
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class SatelliteDataUpdateCoordinator(DataUpdateCoordinator):
    """Data update coordinator for tracking a specific satellite."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.entry = entry
        self.satellite_id = entry.data["norad_id"]
        self.satellite_name = f"Satellite {self.satellite_id}"
        self.tle_url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={self.satellite_id}&FORMAT=tle"

        # Initialize Home Assistant Store helper for persistent disk caching
        self.store = Store(hass, 1, f"{DOMAIN}_{self.satellite_id}")

        # Fetch configured update interval (default to 120s, minimum enforced at 5s)
        update_interval_sec = entry.options.get("update_interval", 120)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.satellite_id}",
            update_interval=timedelta(seconds=update_interval_sec),
        )

        # Listen for option changes so the interval updates live without restarting
        entry.async_on_unload(entry.add_update_listener(self.async_update_listener))

        self.ts = load.timescale()
        self.satellite = None
        self.last_tle_fetch = None

        self.home_observer = wgs84.latlon(
            latitude_degrees=self.hass.config.latitude,
            longitude_degrees=self.hass.config.longitude,
            elevation_m=self.hass.config.elevation
        )

    @staticmethod
    async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Handle options update by reloading the config entry."""
        await hass.config_entries.async_reload(entry.entry_id)

    @override
    async def _async_update_data(self):
        """Fetch TLE, compute position, and find next pass."""
        try:
            now = datetime.now(timezone.utc)

            # 1. If in-memory satellite is not loaded, try loading from disk store first
            if self.satellite is None:
                stored_data = await self.store.async_load()
                if stored_data:
                    try:
                        line1 = stored_data.get("line1")
                        line2 = stored_data.get("line2")
                        name = stored_data.get("name")
                        fetch_ts_str = stored_data.get("timestamp")
                        if line1 and line2 and name and fetch_ts_str:
                            self.satellite = EarthSatellite(line1, line2, name, self.ts)
                            self.last_tle_fetch = datetime.fromisoformat(fetch_ts_str)
                            self.satellite_name = name
                            _LOGGER.debug("Successfully loaded TLE cache from disk for %s", name)
                    except Exception as err:
                        _LOGGER.debug("Failed to parse stored TLE cache: %s", err)

            # 2. Check if we need to fetch a fresh TLE (older than 12 hours or never fetched)
            if self.satellite is None or self.last_tle_fetch is None or (now - self.last_tle_fetch).total_seconds() > 43200:
                success = await self._async_fetch_and_parse_tle()
                if not success and self.satellite is None:
                    if self.data:
                        _LOGGER.warning("Using cached TLE data for %s due to fetch failure.", self.satellite_name)
                        return self.data
                    raise UpdateFailed(f"Failed to download TLE data for {self.satellite_name}.")

            t = self.ts.from_datetime(now)
            geocentric = self.satellite.at(t)
            subpoint = wgs84.subpoint(geocentric)

            lat = float(subpoint.latitude.degrees)
            lon = float(subpoint.longitude.degrees)
            elevation = float(subpoint.elevation.km)

            next_pass = self._calculate_next_pass(now)

            state_val = "In Orbit"
            if next_pass.get("time"):
                pass_dt = datetime.fromisoformat(next_pass["time"])
                time_to_pass = (pass_dt - now).total_seconds()
                if 0 <= time_to_pass <= 900:
                    state_val = f"Approaching ({next_pass.get('event', 'Pass')})"
                elif -300 <= time_to_pass < 0:
                    state_val = "Overhead Now!"

            return {
                "latitude": lat,
                "longitude": lon,
                "altitude_km": elevation,
                "timestamp": now,
                "status": state_val,
                "next_pass_time": next_pass.get("time"),
                "next_pass_event": next_pass.get("event"),
                "next_pass_max_degrees": next_pass.get("max_alt"),
            }

        except Exception as err:
            _LOGGER.warning("Orbital calculation warning for %s (%s).", self.satellite_name, err)
            if self.data:
                return self.data
            raise UpdateFailed(f"Orbital calculation failed: {err}")

    def _calculate_next_pass(self, now: datetime):
        """Find the next upcoming pass over home coordinates."""
        try:
            t0 = self.ts.from_datetime(now)
            t1 = self.ts.from_datetime(now + timedelta(hours=48))

            times, events = self.satellite.find_events(self.home_observer, t0, t1, altitude_degrees=10.0)
            event_names = ["Rise (Above 10°)", "Culmination (Highest Point)", "Set"]

            for i, (ti, event) in enumerate(zip(times, events)):
                event_time = ti.utc_datetime()
                if event_time > now:
                    max_alt = None
                    for future_ti, future_event in zip(times[i:], events[i:]):
                        if future_event == 1:
                            difference = self.satellite - self.home_observer
                            topocentric = difference.at(future_ti)
                            alt, _, _ = topocentric.altaz()
                            max_alt = round(alt.degrees, 1)
                            break

                    if max_alt is None:
                        for future_ti, future_event in zip(times, events):
                            if future_ti.utc_datetime() > now and future_event == 1:
                                difference = self.satellite - self.home_observer
                                topocentric = difference.at(future_ti)
                                alt, _, _ = topocentric.altaz()
                                max_alt = round(alt.degrees, 1)
                                break

                    return {
                        "time": event_time.isoformat(),
                        "event": event_names[event],
                        "max_alt": max_alt
                    }
        except Exception as err:
            _LOGGER.debug("Could not compute next pass: %s", err)

        return {"time": None, "event": "Unknown", "max_alt": None}

    async def _async_fetch_and_parse_tle(self) -> bool:
        """Download, validate, and persistently store TLE from CelesTrak."""
        try:
            headers = {"User-Agent": "HomeAssistant-SatelliteOrbitalTracker/2.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(self.tle_url, headers=headers, timeout=15) as response:
                    if response.status != 200:
                        return False

                    text = await response.text()
                    lines = [line.strip() for line in text.splitlines() if line.strip()]

                    line1 = line2 = None
                    parsed_name = self.satellite_name

                    for line in lines:
                        if line.startswith("1 "):
                            line1 = line
                        elif line.startswith("2 "):
                            line2 = line
                        elif not line.startswith("1 ") and not line.startswith("2 ") and len(line) > 2 and "---" not in line:
                            parsed_name = line

                    if not line1 or not line2:
                        return False

                    self.satellite = EarthSatellite(line1, line2, parsed_name, self.ts)
                    self.last_tle_fetch = datetime.now(timezone.utc)

                    # Persist the newly fetched TLE data to disk via Home Assistant Store
                    await self.store.async_save({
                        "line1": line1,
                        "line2": line2,
                        "name": parsed_name,
                        "timestamp": self.last_tle_fetch.isoformat(),
                    })

                    if parsed_name != self.satellite_name:
                        self.satellite_name = parsed_name
                        hass_entry = self.hass.config_entries.async_get_entry(self.entry.entry_id)
                        if hass_entry:
                            new_data = {**hass_entry.data, "satellite_name": parsed_name}
                            self.hass.config_entries.async_update_entry(hass_entry, title=parsed_name, data=new_data)

                    return True
        except Exception as err:
            _LOGGER.error("TLE fetch exception for %s: %s", self.satellite_name, err)
            return False
