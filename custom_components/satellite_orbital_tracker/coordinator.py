"""Data update coordinator for Satellite Orbital Tracker."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
from typing import override
import aiohttp

from bs4 import BeautifulSoup
import re

from skyfield.api import EarthSatellite, load, wgs84
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# TLE polling, retry intervals, and crew fetch frequency constants
TLE_FETCH_INTERVAL = timedelta(hours=12)      # Normal interval between successful fetches
TLE_RETRY_INTERVAL = timedelta(hours=1)       # Shorter retry interval if the last fetch failed
LOST_SATELLITE_WINDOW = timedelta(hours=24)   # Duration of continuous 404s before raising a repair issue
CREW_FETCH_INTERVAL = timedelta(hours=24)     # Only scrape NASA crew manifest once a day
PASS_MIN_ELEVATION_DEGREES = 10.0             # Elevation above the horizon at which a pass counts as visible

class SatelliteDataUpdateCoordinator(DataUpdateCoordinator):
    """Data update coordinator for tracking a specific satellite."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator and configure tracking endpoints and schedules."""
        self.entry = entry
        self.satellite_id = entry.data["norad_id"]
        self.satellite_name = f"Satellite {self.satellite_id}"
        self.tle_url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={self.satellite_id}&FORMAT=tle"

        # Initialize Home Assistant Store helper for persistent disk caching of TLE elements
        self.store = Store(hass, 1, f"{DOMAIN}_{self.satellite_id}")

        # Fetch configured sensor position update interval (default to 120s, minimum enforced at 5s)
        update_interval_sec = entry.options.get("update_interval", 120)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.satellite_id}",
            update_interval=timedelta(seconds=update_interval_sec),
        )

        # Listen for option changes so the interval updates live without restarting Home Assistant
        entry.async_on_unload(entry.add_update_listener(self.async_update_listener))

        self.ts = load.timescale()
        self.satellite = None
        self.last_tle_fetch = None        # Timestamp of last SUCCESSFUL fetch
        self.last_tle_attempt = None      # Timestamp of last fetch ATTEMPT (success or fail)
        self.last_fetch_status = 200      # Track HTTP status code of the last attempt
        self.first_404_timestamp = None   # Tracks when a continuous 404 missing satellite streak started
        self.crew_members = []            # Cached list of current ISS crew members
        self.last_crew_fetch = None       # Timestamp of last successful crew scrape

        # Set up Home Assistant instance geographic coordinates as default observer for pass calculations
        self.home_observer = wgs84.latlon(
            latitude_degrees=self.hass.config.latitude,
            longitude_degrees=self.hass.config.longitude,
            elevation_m=self.hass.config.elevation
        )

    @staticmethod
    async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Handle options update by reloading the config entry dynamically."""
        await hass.config_entries.async_reload(entry.entry_id)

    @override
    async def _async_update_data(self):
        """Fetch TLE (if interval elapsed), compute satellite subpoint position, and find upcoming passes."""
        try:
            now = datetime.now(timezone.utc)

            # 1. If in-memory satellite object is not loaded, attempt loading from disk store cache first
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

            # 2. Check if we need to fetch a fresh TLE using intelligent interval / backoff logic
            time_since_last_attempt = (now - self.last_tle_attempt) if self.last_tle_attempt else None
            required_interval = TLE_FETCH_INTERVAL if self.last_fetch_status == 200 else TLE_RETRY_INTERVAL

            should_fetch = (
                self.satellite is None
                or self.last_tle_attempt is None
                or time_since_last_attempt > required_interval
            )

            if should_fetch:
                success, status_code = await self._async_fetch_and_parse_tle()
                self.last_tle_attempt = now
                self.last_fetch_status = status_code

                if not success:
                    if self.satellite is not None:
                        # Graceful fallback: log warning and continue using stale cached orbital elements
                        _LOGGER.warning(
                            "Failed to fetch fresh TLE from CelesTrak (Status: %s). Using cached TLE data for %s.",
                            status_code,
                            self.satellite_name
                        )
                    else:
                        # Hard failure if network fails and no local cache exists at all
                        raise UpdateFailed(f"Failed to download TLE data and no cache exists for {self.satellite_name}.")

            # 3. Fetch structured ISS crew manifest once a day if tracking the International Space Station (NORAD ID 25544)
            crew_data = self.crew_members
            if str(self.satellite_id) == "25544":
                time_since_crew_fetch = (now - self.last_crew_fetch) if self.last_crew_fetch else None
                if not self.crew_members or not time_since_crew_fetch or time_since_crew_fetch >= CREW_FETCH_INTERVAL:
                    scraped_crew = await self._async_fetch_iss_crew_from_nasa()
                    if scraped_crew:
                        self.crew_members = scraped_crew
                        self.last_crew_fetch = now
                crew_data = self.crew_members

            # 4. Perform Skyfield vector calculations for current geographic subpoint and pass timings
            t = self.ts.from_datetime(now)
            geocentric = self.satellite.at(t)
            subpoint = wgs84.subpoint(geocentric)

            lat = float(subpoint.latitude.degrees)
            lon = float(subpoint.longitude.degrees)
            elevation = float(subpoint.elevation.km)

            next_pass = self._calculate_next_pass(now)
            current_altitude = self._calculate_current_altitude(t)

            # Determine tracker status string based on the current sky position and upcoming pass proximity
            state_val = "In Orbit"
            time_to_pass = None
            if next_pass.get("time"):
                pass_dt = datetime.fromisoformat(next_pass["time"])
                time_to_pass = (pass_dt - now).total_seconds()

            if current_altitude is not None and current_altitude >= PASS_MIN_ELEVATION_DEGREES:
                # _calculate_next_pass() only returns events that are still in the future, so a pass in
                # progress can only be detected from the satellite's current elevation over the home location.
                state_val = "Overhead Now!"
            elif time_to_pass is not None and 0 <= time_to_pass <= 900:
                state_val = f"Approaching ({next_pass.get('event', 'Pass')})"

            return {
                "latitude": lat,
                "longitude": lon,
                "altitude_km": elevation,
                "timestamp": now,
                "status": state_val,
                "next_pass_time": next_pass.get("time"),
                "next_pass_event": next_pass.get("event"),
                "next_pass_max_degrees": next_pass.get("max_alt"),
                "crew_count": len(crew_data),
                "crew_members": crew_data,
                "next_pass_time_remaining": time_to_pass,
            }

        except Exception as err:
            # Catch unexpected orbital calculation errors and fallback to previous valid state if available
            _LOGGER.warning("Orbital calculation warning for %s (%s).", self.satellite_name, err)
            if self.data:
                return self.data
            raise UpdateFailed(f"Orbital calculation failed: {err}")

    async def _async_fetch_iss_crew_from_nasa(self) -> list[dict]:
        """Scrape structured ISS crew members from NASA's astronaut page under 'At the Space Station'."""
        if str(self.satellite_id) != "25544":
            return []

        url = "https://www.nasa.gov/humans-in-space/astronauts/"
        headers = {"User-Agent": "HomeAssistant-SatelliteOrbitalTracker/2.0"}

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, headers=headers, timeout=15) as response:
                if response.status != 200:
                    return self.crew_members or []

                html_text = await response.text()
                soup = BeautifulSoup(html_text, "html.parser")
                crew_list = []

                # Locate the specific section header for current station occupants
                station_header = soup.find(lambda tag: tag.name in ["h2", "h3"] and "At the Space Station" in tag.text)
                if station_header:
                    module_container = station_header.find_parent("div", class_="hds-module")
                    cards = module_container.find_all("div", class_="hds-meet-the-card") if module_container else soup.find_all("div", class_="hds-meet-the-card")
                else:
                    cards = soup.find_all("div", class_="hds-meet-the-card")

                # Parse individual astronaut profile cards
                for card in cards:
                    name_header = card.find("h3")
                    bio_p = card.find("p")

                    if name_header:
                        name = name_header.get_text(strip=True)
                        bio_text = bio_p.get_text(strip=True) if bio_p else ""

                        agency = "Unknown"
                        if "Roscosmos" in bio_text or "cosmonaut" in bio_text.lower():
                            agency = "Roscosmos"
                        elif "CSA" in bio_text or "Canadian" in bio_text:
                            agency = "CSA"
                        elif "NASA" in bio_text:
                            agency = "NASA"

                        exp_match = re.search(r"Expedition\s+[\d/]+", bio_text, re.IGNORECASE)
                        expedition = exp_match.group(0) if exp_match else None

                        crew_list.append({
                            "name": name,
                            "agency": agency,
                            "title": bio_text,
                            "expedition": expedition
                        })

                if crew_list:
                    return crew_list
        except Exception as err:
            _LOGGER.debug("Failed to parse detailed NASA crew page: %s", err)

        # Fallback to previously cached crew members if scraping fails
        return self.crew_members or []

    def _calculate_current_altitude(self, t) -> float | None:
        """Return the satellite's current elevation above the home observer's horizon, in degrees."""
        try:
            topocentric = (self.satellite - self.home_observer).at(t)
            alt, _, _ = topocentric.altaz()
            return float(alt.degrees)
        except Exception as err:
            _LOGGER.debug("Could not compute current elevation: %s", err)
            return None

    def _calculate_next_pass(self, now: datetime):
        """Find the next upcoming satellite pass over home coordinates using Skyfield ephemeris."""
        try:
            t0 = self.ts.from_datetime(now)
            t1 = self.ts.from_datetime(now + timedelta(hours=48))

            times, events = self.satellite.find_events(
                self.home_observer, t0, t1, altitude_degrees=PASS_MIN_ELEVATION_DEGREES
            )
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

    async def _async_fetch_and_parse_tle(self) -> tuple[bool, int]:
        """Download, validate, and persistently store TLE from CelesTrak with 404/repair tracking."""
        try:
            headers = {"User-Agent": "HomeAssistant-SatelliteOrbitalTracker/2.0"}
            session = async_get_clientsession(self.hass)
            async with session.get(self.tle_url, headers=headers, timeout=15) as response:
                status_code = response.status

                if status_code != 200:
                    if status_code == 404:
                        now = datetime.now(timezone.utc)
                        # Track when the continuous 404 missing satellite outage started
                        if self.first_404_timestamp is None:
                            self.first_404_timestamp = now

                        # Trigger a Home Assistant repair issue if the satellite remains missing beyond 24 hours
                        if (now - self.first_404_timestamp) >= LOST_SATELLITE_WINDOW:  # pragma: no cover
                            ir.async_create_issue(
                                self.hass,
                                DOMAIN,
                                f"satellite_lost_{str(self.satellite_id)}",
                                is_fixable=False,
                                severity=ir.IssueSeverity.WARNING,
                                translation_key="satellite_lost",
                                translation_placeholders={
                                    "satellite_name": self.satellite_name,
                                    "satellite_id": str(self.satellite_id),
                                },
                            )
                        return False, status_code

                    # Any other non-200 status (403 rate limiting, 5xx outage, ...) is simply a failed fetch.
                    # Return here so it neither resets the 404 streak nor clears an existing repair issue,
                    # and so the response body is never parsed as if it were TLE data.
                    _LOGGER.debug("CelesTrak returned HTTP %s for %s", status_code, self.satellite_name)
                    return False, status_code

                # Reset 404 streak tracking and clear any existing repair issue on successful connection
                self.first_404_timestamp = None
                ir.async_delete_issue(self.hass, DOMAIN, f"satellite_lost_{str(self.satellite_id)}")

                text = await response.text()
                lines = [line.strip() for line in text.splitlines() if line.strip()]

                line1 = line2 = None
                parsed_name = self.satellite_name

                # Parse TLE format (Line 1, Line 2, and optional satellite name header)
                for line in lines:
                    if line.startswith("1 "):
                        line1 = line
                    elif line.startswith("2 "):
                        line2 = line
                    elif not line.startswith("1 ") and not line.startswith("2 ") and len(line) > 2 and "---" not in line:
                        parsed_name = line

                if not line1 or not line2:
                    return False, status_code

                # Initialize EarthSatellite model and save locally to persistent storage store
                self.satellite = EarthSatellite(line1, line2, parsed_name, self.ts)
                self.last_tle_fetch = datetime.now(timezone.utc)

                await self.store.async_save({
                    "line1": line1,
                    "line2": line2,
                    "name": parsed_name,
                    "timestamp": self.last_tle_fetch.isoformat(),
                })

                # Dynamically update the config entry title if CelesTrak reports an updated official name
                if parsed_name != self.satellite_name:
                    self.satellite_name = parsed_name
                    hass_entry = self.hass.config_entries.async_get_entry(self.entry.entry_id)
                    if hass_entry:
                        new_data = {**hass_entry.data, "satellite_name": parsed_name}
                        self.hass.config_entries.async_update_entry(hass_entry, title=parsed_name, data=new_data)

                return True, status_code
        except Exception as err:
            _LOGGER.error("TLE fetch exception for %s: %s", self.satellite_name, err)
            return False, 0
