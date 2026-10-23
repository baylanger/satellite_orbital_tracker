"""Tests for the "Next Pass Max Elevation" value, in particular while a pass is under way."""
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    PASS_MIN_ELEVATION_DEGREES,
    SatelliteDataUpdateCoordinator,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2


def _make_coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    return coordinator


def _two_passes(coordinator: SatelliteDataUpdateCoordinator) -> list[tuple[datetime, datetime, datetime, float]]:
    """Return (rise, culmination, set, peak elevation) of the first two complete passes after the TLE epoch."""
    start = coordinator.satellite.epoch.utc_datetime()
    times, events = coordinator.satellite.find_events(
        coordinator.home_observer,
        TEST_TIMESCALE.from_datetime(start),
        TEST_TIMESCALE.from_datetime(start + timedelta(hours=48)),
        altitude_degrees=PASS_MIN_ELEVATION_DEGREES,
    )
    passes = []
    for i in range(len(events) - 2):
        if list(events[i:i + 3]) == [0, 1, 2]:
            peak = (coordinator.satellite - coordinator.home_observer).at(times[i + 1]).altaz()[0].degrees
            passes.append((times[i].utc_datetime(), times[i + 1].utc_datetime(), times[i + 2].utc_datetime(), round(peak, 1)))
    assert len(passes) >= 2, "test TLE should give at least two passes in 48 hours"
    return passes[:2]


async def test_before_a_pass_reports_its_peak(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    (rise, _, _, peak), _ = _two_passes(coordinator)

    result = coordinator._calculate_next_pass(rise - timedelta(minutes=5))

    assert result["event"] == "Rise (Above 10°)"
    assert result["max_alt"] == peak


async def test_during_a_pass_before_its_peak(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    (rise, culmination, _, peak), _ = _two_passes(coordinator)

    result = coordinator._calculate_next_pass(rise + (culmination - rise) / 2)

    assert result["event"] == "Culmination (Highest Point)"
    assert result["max_alt"] == peak


async def test_during_a_pass_after_its_peak_reports_the_current_pass(hass: HomeAssistant) -> None:
    """After the culmination the next event is "Set": the peak is the current pass's, not the next pass's."""
    coordinator = _make_coordinator(hass)
    (_, culmination, set_time, peak), (_, _, _, next_peak) = _two_passes(coordinator)
    assert peak != next_peak, "the two test passes should peak at different elevations"

    result = coordinator._calculate_next_pass(culmination + (set_time - culmination) / 2)

    assert result["event"] == "Set"
    assert result["max_alt"] == peak
