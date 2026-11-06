"""Tests for the Ground Distance and Line-of-Sight Distance sensors."""
import math
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite, wgs84

from custom_components.satellite_orbital_tracker.const import DOMAIN, EARTH_MEAN_RADIUS_KM
from custom_components.satellite_orbital_tracker.coordinator import (
    PASS_MIN_ELEVATION_DEGREES,
    SatelliteDataUpdateCoordinator,
)
from custom_components.satellite_orbital_tracker.sensor import SatelliteGroundDistanceSensor, SatelliteLineOfSightSensor

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    coordinator = SatelliteDataUpdateCoordinator(hass, _entry(hass), TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    return coordinator


@pytest.mark.parametrize(
    ("lat1", "lon1", "lat2", "lon2", "expected"),
    [
        (0, 0, 0, 0, 0.0),
        (0, 0, 0, 90, EARTH_MEAN_RADIUS_KM * math.pi / 2),   # a quarter of the way round the equator
        (0, 0, 90, 0, EARTH_MEAN_RADIUS_KM * math.pi / 2),   # equator to the pole
        (90, 0, -90, 0, EARTH_MEAN_RADIUS_KM * math.pi),     # pole to pole
        (10, 20, -10, -160, EARTH_MEAN_RADIUS_KM * math.pi), # opposite points of the Earth
        (46.8, -71.2, 46.8, -71.2, 0.0),
        (45, 0, 45, 180, EARTH_MEAN_RADIUS_KM * math.pi / 2), # over the pole
        (0, 179, 0, -179, EARTH_MEAN_RADIUS_KM * math.radians(2)),  # across the date line
    ],
)
def test_great_circle(lat1, lon1, lat2, lon2, expected) -> None:
    assert SatelliteDataUpdateCoordinator._great_circle_km(lat1, lon1, lat2, lon2) == pytest.approx(expected, abs=0.01)


def test_ground_distance_known_places() -> None:
    """Québec City to Paris is about 5,250 km along the ground."""
    assert SatelliteDataUpdateCoordinator._great_circle_km(46.81, -71.21, 48.86, 2.35) == pytest.approx(5250, abs=60)


async def test_distances_match_the_orbit(hass: HomeAssistant) -> None:
    """Straight-line distance against the vector between the two positions, ground distance against the central angle."""
    coordinator = _coordinator(hass)
    start = coordinator.satellite.epoch.utc_datetime()
    for hours in (0, 2.2, 5.1, 11.7, 19.3):
        moment = start + timedelta(hours=hours)
        t = TEST_TIMESCALE.from_datetime(moment)
        subpoint = wgs84.subpoint(coordinator.satellite.at(t))
        ground, line_of_sight = coordinator._distances_from_home(t, subpoint.latitude.degrees, subpoint.longitude.degrees)

        home = coordinator.home_observer.at(t).position.km
        satellite = coordinator.satellite.at(t).position.km
        central_angle = math.acos(np.dot(home, satellite) / np.linalg.norm(home) / np.linalg.norm(satellite))
        # The two use geodetic and geocentric latitudes, which differ by up to 0.2 degrees (about 20 km)
        assert ground == pytest.approx(central_angle * EARTH_MEAN_RADIUS_KM, abs=40)

        altitude = (coordinator.satellite - coordinator.home_observer).at(t).altaz()[0].degrees
        if altitude >= 0:
            assert line_of_sight == pytest.approx(np.linalg.norm(satellite - home), rel=1e-6)
            assert line_of_sight >= wgs84.subpoint(coordinator.satellite.at(t)).elevation.km - 1
            assert line_of_sight >= 0
        else:
            assert line_of_sight is None


async def test_line_of_sight_over_a_pass(hass: HomeAssistant) -> None:
    """Shortest near the top of a pass, longer at the ends, and never shorter than the satellite's height."""
    coordinator = _coordinator(hass)
    start = coordinator.satellite.epoch.utc_datetime()
    times, events = coordinator.satellite.find_events(
        coordinator.home_observer, TEST_TIMESCALE.from_datetime(start),
        TEST_TIMESCALE.from_datetime(start + timedelta(hours=24)), altitude_degrees=PASS_MIN_ELEVATION_DEGREES)
    rise = next(ti.utc_datetime() for ti, event in zip(times, events) if event == 0)
    culmination = next(ti.utc_datetime() for ti, event in zip(times, events) if event == 1 and ti.utc_datetime() > rise)
    set_time = next(ti.utc_datetime() for ti, event in zip(times, events) if event == 2 and ti.utc_datetime() > rise)

    def distance_at(moment: datetime) -> float:
        t = TEST_TIMESCALE.from_datetime(moment)
        subpoint = wgs84.subpoint(coordinator.satellite.at(t))
        return coordinator._distances_from_home(t, subpoint.latitude.degrees, subpoint.longitude.degrees)[1]

    at_rise, at_peak, at_set = distance_at(rise + timedelta(seconds=1)), distance_at(culmination), distance_at(set_time - timedelta(seconds=1))
    assert at_peak < at_rise and at_peak < at_set
    assert at_peak > 380  # the test orbit is about 400 km up
    assert 1000 < at_rise < 2200 and 1000 < at_set < 2200  # at 10 degrees of elevation


async def test_update_reports_distances(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    freezer.move_to(now)
    coordinator.last_tle_attempt = now
    coordinator.last_tle_fetch = now

    data = await coordinator._async_update_data()

    assert 0 <= data["ground_distance_km"] <= math.pi * EARTH_MEAN_RADIUS_KM
    altitude = (coordinator.satellite - coordinator.home_observer).at(TEST_TIMESCALE.from_datetime(now)).altaz()[0].degrees
    assert (data["line_of_sight_km"] is not None) == (altitude >= 0)


async def test_distances_unknown_on_error(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator.satellite = None

    ground, line_of_sight = coordinator._distances_from_home(TEST_TIMESCALE.from_datetime(datetime(2026, 1, 10, tzinfo=timezone.utc)), 10.0, 20.0)

    assert ground is not None  # does not need the satellite's orbit
    assert line_of_sight is None


async def test_sensor_values(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    ground, line_of_sight = SatelliteGroundDistanceSensor(coordinator, entry), SatelliteLineOfSightSensor(coordinator, entry)

    coordinator.data = {"ground_distance_km": 812.4, "line_of_sight_km": None}
    assert ground.native_value == 812.4
    assert line_of_sight.native_value is None

    coordinator.data = {"ground_distance_km": 5.0, "line_of_sight_km": 1500.5}
    assert line_of_sight.native_value == 1500.5

    for data in (None, {}, {"ground_distance_km": float("nan"), "line_of_sight_km": float("nan")}):
        coordinator.data = data
        assert ground.native_value is None and line_of_sight.native_value is None


async def _setup(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("object_id", ["ground_distance", "line_of_sight_distance"])
async def test_entities_are_disabled_until_enabled_in_the_ui(hass: HomeAssistant, object_id: str) -> None:
    await _setup(hass)

    entity_id = f"sensor.satellite_css_tianhe_{object_id}"
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


async def test_entities_when_enabled(hass: HomeAssistant) -> None:
    with patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True):  # as if enabled in the UI
        await _setup(hass)

    ground = hass.states.get("sensor.satellite_css_tianhe_ground_distance")
    assert ground is not None
    assert ground.attributes["device_class"] == "distance"
    assert ground.attributes["unit_of_measurement"] == "km"
    assert 0 <= float(ground.state) <= 20100
    line_of_sight = hass.states.get("sensor.satellite_css_tianhe_line_of_sight_distance")
    assert line_of_sight is not None
    assert line_of_sight.state == "unknown" or float(line_of_sight.state) > 380
