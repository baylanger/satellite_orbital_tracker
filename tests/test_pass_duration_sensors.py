"""Tests for the Pass Duration and Pass Time Remaining sensors and the values behind them."""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator
from custom_components.satellite_orbital_tracker.sensor import (
    SatellitePassDurationSensor,
    SatellitePassRemainingSensor,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
OVERHEAD = 30.0  # elevation (degrees) above the 10 degree pass threshold
BELOW = -20.0


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


def _next_pass(start: int | None, end: int | None) -> dict:
    """A next-pass result whose pass starts/ends `start`/`end` seconds from NOW (None: unknown)."""
    def iso(seconds):
        return None if seconds is None else (NOW + timedelta(seconds=seconds)).isoformat()

    return {
        "time": iso(end if start is not None and start <= 0 else start),
        "event": "Set" if start is not None and start <= 0 else "Rise (Above 10°)",
        "max_alt": 55.0,
        "pass_start": iso(start),
        "pass_end": iso(end),
    }


async def _update(hass: HomeAssistant, freezer: FrozenDateTimeFactory, *, altitude: float, next_pass: dict) -> dict:
    """Run one coordinator update with a faked current elevation and next pass."""
    freezer.move_to(NOW)
    coordinator = SatelliteDataUpdateCoordinator(hass, _entry(hass), TEST_TIMESCALE)
    with (
        aioresponses() as m,
        patch.object(SatelliteDataUpdateCoordinator, "_calculate_current_altitude", return_value=altitude),
        patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_pass", return_value=next_pass),
    ):
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        return await coordinator._async_update_data()


@pytest.mark.parametrize(
    ("altitude", "start", "end", "duration", "remaining"),
    [
        # next pass: 30 minutes away, 5 minutes long; nothing is in progress
        (BELOW, 1800, 2100, 300, 0),
        # a pass in progress, 2 minutes after its rise and 3 minutes before its set
        (OVERHEAD, -120, 180, 300, 180),
        # in progress, but its rise could not be found: the end is still known
        (OVERHEAD, None, 90, None, 90),
        # in progress, but its end is not known
        (OVERHEAD, -60, None, None, None),
        # overhead while the search returned the next pass (the one in progress was missed): the end is not this pass's
        (OVERHEAD, 1800, 2100, 300, None),
        # overhead just after the computed set: nothing left
        (OVERHEAD, -300, -1, 299, 0),
        # no pass found at all
        (BELOW, None, None, None, 0),
    ],
    ids=["next-pass", "in-progress", "rise-unknown", "end-unknown", "next-pass-while-overhead", "just-set", "none"],
)
async def test_duration_and_remaining(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, altitude, start, end, duration, remaining
) -> None:
    data = await _update(hass, freezer, altitude=altitude, next_pass=_next_pass(start, end))

    assert data["pass_duration"] == duration
    assert data["pass_time_remaining"] == remaining


async def test_results_without_pass_times_are_still_accepted(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A next-pass result without the pass window (as in older code and tests) gives no duration and nothing remaining."""
    data = await _update(
        hass, freezer, altitude=BELOW, next_pass={"time": (NOW + timedelta(hours=1)).isoformat(), "event": "Rise", "max_alt": 40.0}
    )

    assert data["pass_duration"] is None
    assert data["pass_time_remaining"] == 0
    assert data["pass_start"] is None
    assert data["pass_end"] is None


def _sensors(hass: HomeAssistant, data: dict | None):
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.data = data
    return SatellitePassDurationSensor(coordinator, entry), SatellitePassRemainingSensor(coordinator, entry)


async def test_sensor_values(hass: HomeAssistant) -> None:
    start, end = (NOW - timedelta(seconds=100)).isoformat(), (NOW + timedelta(seconds=200.4)).isoformat()
    duration, remaining = _sensors(
        hass, {"pass_duration": 300.4, "pass_time_remaining": 200.4, "pass_start": start, "pass_end": end}
    )

    assert duration.native_value == 300
    assert remaining.native_value == 200
    assert duration.extra_state_attributes == {"pass_start": start, "pass_end": end}


async def test_sensor_values_when_unknown(hass: HomeAssistant) -> None:
    duration, remaining = _sensors(hass, {"pass_duration": None, "pass_time_remaining": None, "pass_start": None, "pass_end": None})
    assert duration.native_value is None
    assert remaining.native_value is None

    duration, remaining = _sensors(hass, None)  # before the first update
    assert duration.native_value is None
    assert remaining.native_value is None
    assert duration.extra_state_attributes == {}


async def test_sensors_are_durations_in_seconds(hass: HomeAssistant) -> None:
    """Both are created with the integration, as durations in seconds with their own unique IDs."""
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for object_id in ("pass_duration", "pass_time_remaining"):
        state = hass.states.get(f"sensor.satellite_css_tianhe_{object_id}")
        assert state is not None, object_id
        assert state.attributes["device_class"] == "duration"
        assert state.attributes["unit_of_measurement"] == "s"
        assert state.state != "unavailable"
