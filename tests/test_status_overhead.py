"""Tests for the satellite status string: In Orbit / Approaching (...) / Overhead Now!."""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    PASS_MIN_ELEVATION_DEGREES,
    SatelliteDataUpdateCoordinator,
)

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _make_coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)
    return SatelliteDataUpdateCoordinator(hass, config_entry)


def _mock_celestrak(m: aioresponses) -> None:
    m.get(
        re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
        status=200,
        body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        repeat=True,
    )


async def _status_for(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    *,
    altitude: float | None,
    next_pass: dict,
) -> str:
    """Run one coordinator update with a faked current elevation and next pass, and return the status."""
    freezer.move_to(NOW)
    coordinator = _make_coordinator(hass)

    with (
        aioresponses() as m,
        patch.object(SatelliteDataUpdateCoordinator, "_calculate_current_altitude", return_value=altitude),
        patch.object(SatelliteDataUpdateCoordinator, "_calculate_next_pass", return_value=next_pass),
    ):
        _mock_celestrak(m)
        data = await coordinator._async_update_data()

    return data["status"]


def _pass_in(seconds: int, event: str) -> dict:
    return {"time": (NOW + timedelta(seconds=seconds)).isoformat(), "event": event, "max_alt": 55.0}


async def test_status_overhead_now_during_a_pass(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A satellite above the pass threshold is reported as overhead, even though the next event is in the future."""
    status = await _status_for(
        hass, freezer, altitude=45.0, next_pass=_pass_in(120, "Culmination (Highest Point)")
    )
    assert status == "Overhead Now!"


async def test_status_overhead_at_exact_threshold(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The pass threshold itself counts as overhead."""
    status = await _status_for(
        hass, freezer, altitude=PASS_MIN_ELEVATION_DEGREES, next_pass=_pass_in(300, "Set")
    )
    assert status == "Overhead Now!"


async def test_status_approaching_when_pass_starts_soon(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A satellite below the horizon threshold with a rise within 15 minutes is 'Approaching'."""
    status = await _status_for(
        hass, freezer, altitude=-20.0, next_pass=_pass_in(600, "Rise (Above 10°)")
    )
    assert status == "Approaching (Rise (Above 10°))"


async def test_status_in_orbit_when_pass_is_far_away(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """With the next pass more than 15 minutes away the status stays 'In Orbit'."""
    status = await _status_for(
        hass, freezer, altitude=-20.0, next_pass=_pass_in(2 * 3600, "Rise (Above 10°)")
    )
    assert status == "In Orbit"


async def test_status_in_orbit_without_any_pass_and_unknown_altitude(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """If neither a pass nor the current elevation is available the status falls back to 'In Orbit'."""
    status = await _status_for(
        hass, freezer, altitude=None, next_pass={"time": None, "event": "Unknown", "max_alt": None}
    )
    assert status == "In Orbit"


async def test_current_altitude_is_a_valid_elevation(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """The real Skyfield-based helper returns a plausible elevation angle in degrees."""
    freezer.move_to(NOW)
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        _mock_celestrak(m)
        await coordinator._async_update_data()

    altitude = coordinator._calculate_current_altitude(coordinator.ts.from_datetime(NOW))
    assert isinstance(altitude, float)
    assert -90.0 <= altitude <= 90.0


async def test_current_altitude_returns_none_on_error(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Without a loaded satellite the helper returns None instead of raising."""
    freezer.move_to(NOW)
    coordinator = _make_coordinator(hass)
    assert coordinator.satellite is None

    assert coordinator._calculate_current_altitude(coordinator.ts.from_datetime(NOW)) is None
