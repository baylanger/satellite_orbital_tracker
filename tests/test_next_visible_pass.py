"""Tests for the Next Visible Pass sensor: a pass above the horizon, lit by the Sun, over a dark sky at home."""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN, compass_point
from custom_components.satellite_orbital_tracker.coordinator import (
    DARK_SUN_ELEVATION_DEGREES,
    NO_VISIBLE_RECHECK_INTERVAL,
    PASS_MIN_ELEVATION_DEGREES,
    VISIBLE_SEARCH_WINDOW,
    SatelliteDataUpdateCoordinator,
)
from custom_components.satellite_orbital_tracker.sensor import SatelliteNextVisiblePassSensor
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


def _conditions(coordinator: SatelliteDataUpdateCoordinator, moments: list[datetime]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Elevation of the satellite, whether it is lit, and the Sun's elevation at home, for each moment.

    Written separately from the code under test: one Skyfield call per quantity.
    """
    t = TEST_TIMESCALE.from_datetimes(moments)
    altitude = (coordinator.satellite - coordinator.home_observer).at(t).altaz()[0].degrees
    sun = sun_direction(t.tt)
    lit = is_sunlit(coordinator.satellite.at(t).position.km, sun)
    zenith = np.array([coordinator.home_observer.rotation_at(ti) @ sun[:, i] for i, ti in enumerate(t)])[:, 2]
    return altitude, lit, np.degrees(np.arcsin(zenith))


def _visible(coordinator: SatelliteDataUpdateCoordinator, moments: list[datetime]) -> np.ndarray:
    altitude, lit, sun_elevation = _conditions(coordinator, moments)
    return (altitude >= PASS_MIN_ELEVATION_DEGREES) & lit & (sun_elevation <= DARK_SUN_ELEVATION_DEGREES)


async def test_next_visible_pass_is_visible_and_the_first(hass: HomeAssistant) -> None:
    """Inside the reported window the satellite is up, lit and the sky dark; no earlier moment qualifies."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()

    result = coordinator._calculate_next_visible_pass(now)

    start, end = datetime.fromisoformat(result["start"]), datetime.fromisoformat(result["end"])
    assert now < start < end <= now + VISIBLE_SEARCH_WINDOW
    assert result["duration"] == pytest.approx((end - start).total_seconds())

    # visible throughout (a little inside the ends, which come from a 2 s sampling)
    inside = [start + timedelta(seconds=3) + (end - start - timedelta(seconds=6)) * i / 9 for i in range(10)]
    assert _visible(coordinator, inside).all()

    # nothing visible before it, checked every 15 s from now on with separate code
    earlier = [now + timedelta(seconds=15 * i) for i in range(int((start - now).total_seconds() // 15) - 1)]
    assert not _visible(coordinator, earlier).any()


async def test_next_visible_pass_details(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()

    result = coordinator._calculate_next_visible_pass(now)

    start, end = datetime.fromisoformat(result["start"]), datetime.fromisoformat(result["end"])
    peak_time = datetime.fromisoformat(result["max_elevation_time"])
    assert start <= peak_time <= end
    assert datetime.fromisoformat(result["pass_start"]) <= start and end <= datetime.fromisoformat(result["pass_end"]) + timedelta(seconds=3)

    altitude, _, _ = _conditions(coordinator, [peak_time])
    assert result["max_elevation"] == pytest.approx(altitude[0], abs=0.1)
    assert PASS_MIN_ELEVATION_DEGREES <= result["max_elevation"] < 90

    for key, moment in (("start_azimuth", start), ("end_azimuth", end)):
        azimuth = (coordinator.satellite - coordinator.home_observer).at(TEST_TIMESCALE.from_datetime(moment)).altaz()[1].degrees
        assert result[key] == pytest.approx(azimuth, abs=3)  # the pass moves about a degree a second, samples are 2 s apart


async def test_a_visible_pass_under_way_is_not_the_next_one(hass: HomeAssistant) -> None:
    """Asked in the middle of the visible window, the search reports a later pass."""
    coordinator = _coordinator(hass)
    first = coordinator._calculate_next_visible_pass(coordinator.satellite.epoch.utc_datetime())
    start, end = datetime.fromisoformat(first["start"]), datetime.fromisoformat(first["end"])

    second = coordinator._calculate_next_visible_pass(start + (end - start) / 2)

    assert datetime.fromisoformat(second["start"]) > end


async def test_the_part_of_a_pass_still_to_come_counts(hass: HomeAssistant) -> None:
    """A pass that has begun but is not visible yet (say, still in shadow) is found by looking back for its rise."""
    coordinator = _coordinator(hass)
    first = coordinator._calculate_next_visible_pass(coordinator.satellite.epoch.utc_datetime())
    start = datetime.fromisoformat(first["start"])
    pass_start = datetime.fromisoformat(first["pass_start"])
    now = (pass_start + start) / 2 if start - pass_start > timedelta(seconds=10) else start - timedelta(seconds=1)

    result = coordinator._calculate_next_visible_pass(now)

    assert abs(datetime.fromisoformat(result["start"]) - datetime.fromisoformat(first["start"])) < timedelta(seconds=2)


async def test_no_visible_pass(hass: HomeAssistant) -> None:
    """A failing search, or a satellite that is never lit while up, gives no pass."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()

    with patch.object(SatelliteDataUpdateCoordinator, "_visible_samples", side_effect=ValueError("boom")):
        assert coordinator._calculate_next_visible_pass(now) == {"start": None}
    with patch.object(SatelliteDataUpdateCoordinator, "_home_sun_elevation", return_value=30.0):  # always day
        assert coordinator._calculate_next_visible_pass(now) == {"start": None}


async def test_daylight_shortcut_does_not_change_the_answer(hass: HomeAssistant) -> None:
    """Skipping passes in broad daylight is only a speed-up: the same pass is found without it, with more sampling."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    sampled = []
    real = SatelliteDataUpdateCoordinator._visible_samples

    def counting(self, start, end):
        sampled.append(start)
        return real(self, start, end)

    with patch.object(SatelliteDataUpdateCoordinator, "_visible_samples", counting):
        with_shortcut = coordinator._calculate_next_visible_pass(now)
        with_count = len(sampled)
        sampled.clear()
        with patch("custom_components.satellite_orbital_tracker.coordinator.VISIBLE_DAYLIGHT_MARGIN_DEGREES", 1000.0):
            without_shortcut = coordinator._calculate_next_visible_pass(now)

    assert with_shortcut == without_shortcut
    assert with_count < len(sampled)


async def test_cache_until_the_pass_starts(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    calls = []
    real = SatelliteDataUpdateCoordinator._calculate_next_visible_pass

    def counting(self, when):
        calls.append(when)
        return real(self, when)

    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_visible_pass", counting):
        first = await coordinator._async_get_next_visible_pass(now)
        start = datetime.fromisoformat(first["start"])
        await coordinator._async_get_next_visible_pass(now + timedelta(minutes=30))
        await coordinator._async_get_next_visible_pass(start - timedelta(seconds=1))
        assert len(calls) == 1

        later = await coordinator._async_get_next_visible_pass(start + timedelta(seconds=1))
        assert len(calls) == 2
        assert datetime.fromisoformat(later["start"]) > start


async def test_cache_after_a_new_tle(hass: HomeAssistant) -> None:
    """A new TLE (a new satellite object) refines the prediction at once."""
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_visible_pass", return_value={"start": "2026-01-20T00:00:00+00:00"}) as search:
        await coordinator._async_get_next_visible_pass(now)
        await coordinator._async_get_next_visible_pass(now)
        assert search.call_count == 1
        coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
        await coordinator._async_get_next_visible_pass(now)
        assert search.call_count == 2


async def test_nothing_found_is_searched_again_after_an_hour(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_visible_pass", return_value={"start": None}) as search:
        await coordinator._async_get_next_visible_pass(now)
        await coordinator._async_get_next_visible_pass(now + NO_VISIBLE_RECHECK_INTERVAL - timedelta(seconds=1))
        assert search.call_count == 1
        await coordinator._async_get_next_visible_pass(now + NO_VISIBLE_RECHECK_INTERVAL)
        assert search.call_count == 2


@pytest.mark.parametrize(
    ("azimuth", "expected"),
    [(0, "N"), (11.2, "N"), (11.3, "NNE"), (45, "NE"), (90, "E"), (135, "SE"), (180, "S"), (225, "SW"),
     (270, "W"), (315, "NW"), (348.7, "NNW"), (348.8, "N"), (359.9, "N"), (360, "N"), (-90, "W"), (450, "E")],
)
def test_compass_point(azimuth: float, expected: str) -> None:
    assert compass_point(azimuth) == expected


def _sensor(hass: HomeAssistant, data: dict | None) -> SatelliteNextVisiblePassSensor:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.data = data
    return SatelliteNextVisiblePassSensor(coordinator, entry)


async def test_sensor_values(hass: HomeAssistant) -> None:
    visible_pass = {
        "start": "2026-10-30T22:10:05+00:00", "end": "2026-10-30T22:14:35.4+00:00", "duration": 270.4,
        "max_elevation": 62.3, "max_elevation_time": "2026-10-30T22:12:20+00:00",
        "start_azimuth": 312.0, "end_azimuth": 118.5,
        "pass_start": "2026-10-30T22:09:50+00:00", "pass_end": "2026-10-30T22:15:10+00:00",
    }
    sensor = _sensor(hass, {"next_visible_pass": visible_pass})

    assert sensor.native_value == datetime(2026, 10, 30, 22, 10, 5, tzinfo=timezone.utc)
    assert sensor.extra_state_attributes == {
        "visible_end": "2026-10-30T22:14:35.4+00:00",
        "duration": 270,
        "max_elevation": 62.3,
        "max_elevation_time": "2026-10-30T22:12:20+00:00",
        "start_azimuth": 312.0,
        "start_direction": "NW",
        "end_azimuth": 118.5,
        "end_direction": "ESE",
        "pass_start": "2026-10-30T22:09:50+00:00",
        "pass_end": "2026-10-30T22:15:10+00:00",
    }

    for data in (None, {}, {"next_visible_pass": None}, {"next_visible_pass": {"start": None}}):
        sensor = _sensor(hass, data)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


async def _setup(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_entity_is_disabled_until_enabled_in_the_ui(hass: HomeAssistant) -> None:
    await _setup(hass)

    registry_entry = er.async_get(hass).async_get("sensor.satellite_css_tianhe_next_visible_pass")
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.satellite_css_tianhe_next_visible_pass") is None


async def test_entity_when_enabled(hass: HomeAssistant) -> None:
    with patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True):  # as if enabled in the UI
        await _setup(hass)

    state = hass.states.get("sensor.satellite_css_tianhe_next_visible_pass")
    assert state is not None
    assert state.attributes["device_class"] == "timestamp"
    if state.state not in ("unknown", "unavailable"):  # a pass is found with the TLE's age (it depends on today's date)
        assert datetime.fromisoformat(state.state) > datetime.now(timezone.utc)
        assert state.attributes["start_direction"] in ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
