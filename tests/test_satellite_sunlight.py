"""Tests for the Sun direction, the Earth's shadow and the sensors that tell when a satellite is sunlit."""
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
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.binary_sensor import SatelliteSunlitSensor
from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    NO_SHADOW_RECHECK_INTERVAL,
    SatelliteDataUpdateCoordinator,
)
from custom_components.satellite_orbital_tracker.sensor import SatelliteSunriseSensor, SatelliteSunsetSensor
from custom_components.satellite_orbital_tracker.sun import is_sunlit, sun_direction

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


def _angle_deg(a, b) -> float:
    """Angle between two vectors, which need not have length 1."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return math.degrees(math.atan2(np.linalg.norm(np.cross(a, b)), np.dot(a, b)))


# Sun directions (GCRS) from JPL's DE441 ephemeris, through Skyfield
@pytest.mark.parametrize(
    ("moment", "reference"),
    [
        (datetime(1969, 7, 26, 12, tzinfo=timezone.utc), (-0.555434, 0.762922, 0.330822)),
        (datetime(1969, 7, 27, 3, tzinfo=timezone.utc), (-0.564068, 0.757571, 0.328502)),
        (datetime(1969, 7, 28, 20, tzinfo=timezone.utc), (-0.587358, 0.742523, 0.321977)),
        (datetime(1969, 7, 29, 9, tzinfo=timezone.utc), (-0.594646, 0.737625, 0.319853)),
    ],
)
def test_sun_direction_matches_the_ephemeris(moment: datetime, reference: tuple) -> None:
    mine = sun_direction(TEST_TIMESCALE.from_datetime(moment).tt)[:, 0]

    assert _angle_deg(mine, reference) < 0.01


def test_sun_direction_at_the_equinox() -> None:
    """At the March equinox the Sun is on the celestial equator; in the J2000 frame its right ascension is
    about 0.34 degrees short of 0 because of 26 years of precession."""
    sun = sun_direction(TEST_TIMESCALE.utc(2026, 3, 20, 14, 46).tt)[:, 0]

    assert math.degrees(math.atan2(sun[1], sun[0])) % 360 == pytest.approx(359.66, abs=0.05)
    assert math.degrees(math.asin(sun[2])) == pytest.approx(0, abs=0.2)
    assert np.linalg.norm(sun) == pytest.approx(1)


@pytest.mark.parametrize(
    ("position", "sunlit"),
    [
        ((7000, 0, 0), True),      # on the day side
        ((-7000, 0, 0), False),    # straight behind the Earth
        ((0, 7000, 0), True),      # above the terminator, outside the shadow cylinder
        ((-7000, 6000, 0), False),  # night side, inside the cylinder (6000 km from the axis)
        ((-7000, 6500, 0), True),   # night side, just outside the cylinder (6500 > 6378 km)
        ((-1000, 0, 7000), True),
    ],
)
def test_shadow_geometry(position: tuple, sunlit: bool) -> None:
    sun = np.array([[1.0], [0.0], [0.0]])

    assert bool(is_sunlit(np.array(position, dtype=float).reshape(3, 1), sun)[0]) is sunlit


def test_shadow_search_finds_both_crossings(hass: HomeAssistant) -> None:
    """Right before the sunrise the satellite is in shadow, right after it is lit, and the reverse for the sunset."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()

    result = coordinator._calculate_shadow_events(now)

    sunrise, sunset = (datetime.fromisoformat(result[key]) for key in ("sunrise", "sunset"))
    assert now < sunrise < now + timedelta(hours=24) and now < sunset < now + timedelta(hours=24)

    def lit_at(moment: datetime) -> bool:
        return bool(coordinator._sunlit_at(np.array([TEST_TIMESCALE.from_datetime(moment).tt]))[0])

    assert not lit_at(sunrise - timedelta(seconds=2)) and lit_at(sunrise + timedelta(seconds=2))
    assert lit_at(sunset - timedelta(seconds=2)) and not lit_at(sunset + timedelta(seconds=2))


def test_shadow_search_with_no_crossing(hass: HomeAssistant) -> None:
    """A satellite in sunlight the whole time (or a failing search) gives no times."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()

    with patch.object(SatelliteDataUpdateCoordinator, "_sunlit_at", return_value=np.ones(2881, dtype=bool)):
        assert coordinator._calculate_shadow_events(now) == {"sunrise": None, "sunset": None}
    with patch.object(SatelliteDataUpdateCoordinator, "_sunlit_at", side_effect=ValueError("boom")):
        assert coordinator._calculate_shadow_events(now) == {"sunrise": None, "sunset": None}


async def test_update_reports_sunlight(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """End to end: the state now agrees with the times to come (a sunset comes first while lit)."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    freezer.move_to(now)
    coordinator.last_tle_attempt = now
    coordinator.last_tle_fetch = now

    data = await coordinator._async_update_data()

    sunrise, sunset = datetime.fromisoformat(data["next_sunrise"]), datetime.fromisoformat(data["next_sunset"])
    assert data["sunlit"] is (sunset < sunrise)


async def test_shadow_search_is_cached_until_a_time_passes(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    calls = []
    real = SatelliteDataUpdateCoordinator._calculate_shadow_events

    def counting(self, when):
        calls.append(when)
        return real(self, when)

    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_shadow_events", counting):
        first = await coordinator._async_get_shadow_events(now)
        sunrise, sunset = (datetime.fromisoformat(first[key]) for key in ("sunrise", "sunset"))
        await coordinator._async_get_shadow_events(now + timedelta(seconds=5))
        await coordinator._async_get_shadow_events(min(sunrise, sunset) - timedelta(seconds=1))
        assert len(calls) == 1

        later = await coordinator._async_get_shadow_events(min(sunrise, sunset) + timedelta(seconds=1))
        assert len(calls) == 2
        assert later != first


async def test_missing_crossing_is_searched_again_after_an_hour(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    calls = []

    def nothing(self, when):
        calls.append(when)
        return {"sunrise": None, "sunset": None}

    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_shadow_events", nothing):
        await coordinator._async_get_shadow_events(now)
        await coordinator._async_get_shadow_events(now + NO_SHADOW_RECHECK_INTERVAL - timedelta(seconds=1))
        assert len(calls) == 1
        await coordinator._async_get_shadow_events(now + NO_SHADOW_RECHECK_INTERVAL)
        assert len(calls) == 2


async def test_sensor_values(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    sunrise, sunset, sunlit = (
        SatelliteSunriseSensor(coordinator, entry),
        SatelliteSunsetSensor(coordinator, entry),
        SatelliteSunlitSensor(coordinator, entry),
    )

    coordinator.data = {"next_sunrise": "2026-10-11T12:30:00+00:00", "next_sunset": None, "sunlit": False}
    assert sunrise.native_value == datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc)
    assert sunset.native_value is None
    assert sunlit.is_on is False

    for data in (None, {}):
        coordinator.data = data
        assert sunrise.native_value is None and sunset.native_value is None and sunlit.is_on is None


async def _setup(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "entity_id",
    [
        "sensor.satellite_css_tianhe_next_satellite_sunrise",
        "sensor.satellite_css_tianhe_next_satellite_sunset",
        "binary_sensor.satellite_css_tianhe_sunlit",
    ],
)
async def test_sunlight_entities_are_disabled_until_enabled_in_the_ui(hass: HomeAssistant, entity_id: str) -> None:
    await _setup(hass)

    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


async def test_sunlight_entities_when_enabled(hass: HomeAssistant) -> None:
    with patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True):  # as if enabled in the UI
        await _setup(hass)

    for entity_id in ("sensor.satellite_css_tianhe_next_satellite_sunrise", "sensor.satellite_css_tianhe_next_satellite_sunset"):
        state = hass.states.get(entity_id)
        assert state is not None and state.attributes["device_class"] == "timestamp"
        assert datetime.fromisoformat(state.state) > datetime.now(timezone.utc) - timedelta(days=1)
    assert hass.states.get("binary_sensor.satellite_css_tianhe_sunlit").state in ("on", "off")
    assert hass.states.get("binary_sensor.satellite_css_tianhe_sunlit").attributes["device_class"] == "light"
