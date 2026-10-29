"""Tests for the Sun's elevation at home and the Visible Now sensor."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite, wgs84

from custom_components.satellite_orbital_tracker.binary_sensor import SatelliteVisibleNowSensor
from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    DARK_SUN_ELEVATION_DEGREES,
    PASS_MIN_ELEVATION_DEGREES,
    SatelliteDataUpdateCoordinator,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
OVERHEAD = 30.0
BELOW = -20.0


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    coordinator = SatelliteDataUpdateCoordinator(hass, _entry(hass), TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    return coordinator


# Elevation of the Sun at 46.8 N, 71.2 W, 60 m, from JPL's DE441 ephemeris through Skyfield
@pytest.mark.parametrize(
    ("moment", "elevation"),
    [
        (datetime(1969, 7, 26, 12, tzinfo=timezone.utc), 25.6768),
        (datetime(1969, 7, 27, 3, tzinfo=timezone.utc), -19.3243),
        (datetime(1969, 7, 28, 20, tzinfo=timezone.utc), 42.5283),
        (datetime(1969, 7, 29, 9, tzinfo=timezone.utc), -3.8829),
        (datetime(1969, 7, 29, 2, tzinfo=timezone.utc), -13.8995),
    ],
)
async def test_home_sun_elevation_matches_the_ephemeris(hass: HomeAssistant, moment: datetime, elevation: float) -> None:
    coordinator = _coordinator(hass)
    coordinator.home_observer = wgs84.latlon(46.8, -71.2, elevation_m=60)

    result = coordinator._home_sun_elevation(TEST_TIMESCALE.from_datetime(moment))

    assert result == pytest.approx(elevation, abs=0.02)


async def test_home_sun_elevation_follows_the_day(hass: HomeAssistant) -> None:
    """Around the March equinox the Sun is about 90 degrees minus the latitude high at noon, and far below at midnight."""
    coordinator = _coordinator(hass)
    coordinator.home_observer = wgs84.latlon(46.8, 0.0)
    noon = coordinator._home_sun_elevation(TEST_TIMESCALE.utc(2026, 3, 20, 12, 7))
    midnight = coordinator._home_sun_elevation(TEST_TIMESCALE.utc(2026, 3, 20, 0, 7))

    assert noon == pytest.approx(90 - 46.8, abs=0.7)
    assert midnight == pytest.approx(-(90 - 46.8), abs=0.7)


@pytest.mark.parametrize(
    ("altitude", "sunlit", "sun_elevation", "expected"),
    [
        (OVERHEAD, True, -20.0, True),
        (OVERHEAD, True, DARK_SUN_ELEVATION_DEGREES, True),         # exactly at the limit counts as dark
        (OVERHEAD, True, DARK_SUN_ELEVATION_DEGREES + 0.1, False),  # twilight is still too bright
        (OVERHEAD, True, 30.0, False),                              # daytime
        (OVERHEAD, False, -20.0, False),                            # in the Earth's shadow
        (PASS_MIN_ELEVATION_DEGREES, True, -20.0, True),            # just risen above the pass limit
        (PASS_MIN_ELEVATION_DEGREES - 0.1, True, -20.0, False),     # not in a pass
        (BELOW, True, -20.0, False),
        (None, True, -20.0, None),                                  # unknown
        (OVERHEAD, None, -20.0, None),
        (OVERHEAD, True, None, None),
    ],
)
def test_visible_now_rule(altitude, sunlit, sun_elevation, expected) -> None:
    assert SatelliteDataUpdateCoordinator._visible_now(altitude, sunlit, sun_elevation) is expected


async def test_update_reports_visibility(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """End to end, with a pass in progress: visible exactly when the satellite is lit and the sky at home is dark."""
    coordinator = _coordinator(hass)
    start = coordinator.satellite.epoch.utc_datetime()
    times, events = coordinator.satellite.find_events(
        coordinator.home_observer,
        TEST_TIMESCALE.from_datetime(start),
        TEST_TIMESCALE.from_datetime(start + timedelta(hours=48)),
        altitude_degrees=PASS_MIN_ELEVATION_DEGREES,
    )
    culmination = next(times[i].utc_datetime() for i, event in enumerate(events) if event == 1)
    freezer.move_to(culmination)
    coordinator.last_tle_attempt = culmination
    coordinator.last_tle_fetch = culmination

    data = await coordinator._async_update_data()

    assert data["status"] == "Overhead Now!"
    assert data["visible_now"] is (data["sunlit"] and data["home_sun_elevation"] <= DARK_SUN_ELEVATION_DEGREES)


def _sensor(hass: HomeAssistant, data: dict | None) -> SatelliteVisibleNowSensor:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.data = data
    return SatelliteVisibleNowSensor(coordinator, entry)


async def test_sensor_values(hass: HomeAssistant) -> None:
    sensor = _sensor(hass, {"visible_now": True, "home_sun_elevation": -12.34, "sunlit": True, "status": "Overhead Now!"})
    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "sun_elevation_at_home": -12.3,
        "dark_below_sun_elevation": DARK_SUN_ELEVATION_DEGREES,
        "satellite_sunlit": True,
        "status": "Overhead Now!",
    }

    unknown = _sensor(hass, {"visible_now": None, "home_sun_elevation": None, "sunlit": None, "status": "In Orbit"})
    assert unknown.is_on is None
    assert unknown.extra_state_attributes["sun_elevation_at_home"] is None

    empty = _sensor(hass, None)
    assert empty.is_on is None and empty.extra_state_attributes == {}


async def _setup(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_visible_now_is_enabled_at_installation(hass: HomeAssistant) -> None:
    """Unlike the other sunlight sensors, Visible Now is enabled when the satellite is added."""
    await _setup(hass)

    registry_entry = er.async_get(hass).async_get("binary_sensor.satellite_css_tianhe_visible_now")
    assert registry_entry is not None
    assert registry_entry.disabled_by is None
    state = hass.states.get("binary_sensor.satellite_css_tianhe_visible_now")
    assert state is not None
    assert state.state in ("on", "off")
    assert isinstance(state.attributes["sun_elevation_at_home"], float)
