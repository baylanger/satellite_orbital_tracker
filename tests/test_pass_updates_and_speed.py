"""Tests for the faster updates during a pass and for the Orbital Speed sensor."""
import math
import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    PASS_MIN_ELEVATION_DEGREES,
    PASS_UPDATE_INTERVAL,
    SatelliteDataUpdateCoordinator,
)
from custom_components.satellite_orbital_tracker.sensor import SatelliteSpeedSensor

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
EARTH_MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.137


def _entry(hass: HomeAssistant, interval: int = 120) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": interval})
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant, interval: int = 120) -> SatelliteDataUpdateCoordinator:
    coordinator = SatelliteDataUpdateCoordinator(hass, _entry(hass, interval), TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    return coordinator


def _first_pass(coordinator: SatelliteDataUpdateCoordinator) -> tuple[datetime, datetime]:
    """Return the rise and set of the first complete pass after the TLE epoch."""
    start = coordinator.satellite.epoch.utc_datetime()
    times, events = coordinator.satellite.find_events(
        coordinator.home_observer,
        TEST_TIMESCALE.from_datetime(start),
        TEST_TIMESCALE.from_datetime(start + timedelta(hours=48)),
        altitude_degrees=PASS_MIN_ELEVATION_DEGREES,
    )
    for i in range(len(events) - 2):
        if list(events[i:i + 3]) == [0, 1, 2]:
            return times[i].utc_datetime(), times[i + 2].utc_datetime()
    raise AssertionError("test TLE should give a pass in 48 hours")


async def _update_at(coordinator: SatelliteDataUpdateCoordinator, freezer: FrozenDateTimeFactory, when: datetime) -> dict:
    """Run a real update (nothing faked) at `when`, with a current TLE so nothing is downloaded."""
    freezer.move_to(when)
    coordinator.last_tle_attempt = when
    coordinator.last_tle_fetch = when
    return await coordinator._async_update_data()


@pytest.mark.parametrize(
    ("configured", "offset_from_rise_s", "expected_s"),
    [
        (120, 100, 5),      # in the pass (rise + 100 s): fast
        (30, 100, 5),
        (5, 100, 5),        # already as fast as it gets
        (120, -3600, 120),  # an hour before the pass: configured interval
        (120, -60, 61),     # a minute before: wake up at the rise
        (30, -60, 30),      # the rise is further away than one interval: configured interval
        (120, -0.2, 1.2),   # about to rise: about a second
    ],
)
async def test_update_interval_follows_the_pass(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, configured: int, offset_from_rise_s: float, expected_s: float
) -> None:
    coordinator = _coordinator(hass, configured)
    rise, _ = _first_pass(coordinator)

    await _update_at(coordinator, freezer, rise + timedelta(seconds=offset_from_rise_s))

    # The rise time from the orbit search is not a whole second, so allow for that
    assert coordinator.update_interval.total_seconds() == pytest.approx(expected_s, abs=1.5)
    assert coordinator.configured_update_interval == timedelta(seconds=configured)


async def test_update_interval_returns_to_configured_after_the_pass(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    rise, set_time = _first_pass(coordinator)

    await _update_at(coordinator, freezer, rise + (set_time - rise) / 2)
    assert coordinator.update_interval == PASS_UPDATE_INTERVAL

    await _update_at(coordinator, freezer, set_time + timedelta(seconds=10))
    assert coordinator.update_interval == timedelta(seconds=120)


async def test_fast_updates_do_not_search_the_next_pass_again(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The 48-hour search is cached, so updating every few seconds during a pass only recomputes the position."""
    coordinator = _coordinator(hass)
    rise, set_time = _first_pass(coordinator)
    start = rise + timedelta(seconds=30)

    await _update_at(coordinator, freezer, start)
    with patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_pass", side_effect=AssertionError("searched again")):
        for seconds in (5, 10, 15):
            data = await _update_at(coordinator, freezer, start + timedelta(seconds=seconds))
            assert data["pass_time_remaining"] == pytest.approx((set_time - (start + timedelta(seconds=seconds))).total_seconds(), abs=2)


async def test_remaining_time_counts_down_with_each_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    rise, set_time = _first_pass(coordinator)
    start = rise + (set_time - rise) / 2

    first = await _update_at(coordinator, freezer, start)
    second = await _update_at(coordinator, freezer, start + timedelta(seconds=5))

    assert first["pass_time_remaining"] - second["pass_time_remaining"] == pytest.approx(5, abs=0.1)


async def test_failed_calculation_goes_back_to_the_configured_interval(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    rise, set_time = _first_pass(coordinator)
    coordinator.data = await _update_at(coordinator, freezer, rise + (set_time - rise) / 2)
    assert coordinator.update_interval == PASS_UPDATE_INTERVAL

    with patch.object(SatelliteDataUpdateCoordinator, "_async_get_next_pass", AsyncMock(side_effect=ValueError("boom"))):
        await _update_at(coordinator, freezer, rise + (set_time - rise) / 2 + timedelta(seconds=5))

    assert coordinator.update_interval == timedelta(seconds=120)


async def test_changing_the_interval_dynamically_does_not_reload(hass: HomeAssistant) -> None:
    """The listener compares with the configured interval, not the one adjusted for the pass."""
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.update_interval = PASS_UPDATE_INTERVAL

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await coordinator.async_update_listener(hass, entry)  # e.g. the title update after a TLE download
        reload.assert_not_awaited()

        hass.config_entries.async_update_entry(entry, options={"update_interval": 300})
        await hass.async_block_till_done()  # the listener registered by the coordinator runs
        reload.assert_awaited_once()


async def test_speed_is_orbital_speed(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The speed comes from the satellite's velocity and agrees with the circular-orbit speed at its height."""
    coordinator = _coordinator(hass)
    data = await _update_at(coordinator, freezer, coordinator.satellite.epoch.utc_datetime())

    circular = math.sqrt(EARTH_MU_KM3_S2 / (EARTH_RADIUS_KM + data["altitude_km"]))
    assert 7.0 < data["speed_km_s"] < 8.0
    assert data["speed_km_s"] == pytest.approx(circular, rel=0.01)


async def test_speed_sensor_values(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    sensor = SatelliteSpeedSensor(coordinator, entry)

    coordinator.data = {"speed_km_s": 7.66}
    assert sensor.native_value == pytest.approx(27576.0)
    assert sensor.extra_state_attributes == {"speed_km_s": 7.66}

    for data in (None, {}, {"speed_km_s": None}, {"speed_km_s": float("nan")}):
        coordinator.data = data
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


async def test_speed_entity_is_a_speed_in_km_per_h(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with (
        aioresponses() as m,
        patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True),  # as if enabled in the UI
    ):
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.satellite_css_tianhe_orbital_speed")
    assert state is not None
    assert state.attributes["device_class"] == "speed"
    assert state.attributes["unit_of_measurement"] == "km/h"
    assert 25000 < float(state.state) < 29000


@pytest.mark.parametrize("object_id", ["orbital_speed", "orbital_altitude"])
async def test_speed_and_altitude_are_disabled_until_enabled_in_the_ui(hass: HomeAssistant, object_id: str) -> None:
    """New installs get these two sensors disabled, so they don't write a state every 5 seconds during a pass."""
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entity_id = f"sensor.satellite_css_tianhe_{object_id}"
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None
