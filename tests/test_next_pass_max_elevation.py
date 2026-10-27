"""Tests for the next pass: its peak elevation and its start and end, in particular while a pass is under way."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    PASS_MIN_ELEVATION_DEGREES,
    PASS_SEARCH_MARGIN,
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


def _assert_same_time(iso: str | None, expected: datetime) -> None:
    """The pass times come from separate Skyfield searches, so allow a couple of seconds of difference."""
    assert iso is not None
    assert abs(datetime.fromisoformat(iso) - expected) < timedelta(seconds=2)


async def test_pass_window_before_a_pass(hass: HomeAssistant) -> None:
    """Before a pass, its start is the next event and its end is that pass's "Set"."""
    coordinator = _make_coordinator(hass)
    (rise, _, set_time, _), _ = _two_passes(coordinator)

    result = coordinator._calculate_next_pass(rise - timedelta(minutes=5))

    _assert_same_time(result["pass_start"], rise)
    _assert_same_time(result["pass_end"], set_time)


async def test_pass_window_during_a_pass_before_its_peak(hass: HomeAssistant) -> None:
    """The rise is already in the past, so it is found by looking back."""
    coordinator = _make_coordinator(hass)
    (rise, culmination, set_time, _), _ = _two_passes(coordinator)

    result = coordinator._calculate_next_pass(rise + (culmination - rise) / 2)

    _assert_same_time(result["pass_start"], rise)
    _assert_same_time(result["pass_end"], set_time)


async def test_pass_window_during_a_pass_after_its_peak(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    (rise, culmination, set_time, _), _ = _two_passes(coordinator)

    result = coordinator._calculate_next_pass(culmination + (set_time - culmination) / 2)

    _assert_same_time(result["pass_start"], rise)
    _assert_same_time(result["pass_end"], set_time)


async def test_no_pass_found_has_no_window(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.satellite = None  # the search fails, which is reported as "no pass"

    assert coordinator._calculate_next_pass(datetime(2026, 10, 11, tzinfo=timezone.utc)) == {
        "time": None, "event": "Unknown", "max_alt": None, "pass_start": None, "pass_end": None,
    }


async def test_search_reaches_past_48_hours_to_find_the_end_of_a_late_pass(hass: HomeAssistant) -> None:
    """A pass that starts just before the 48-hour mark must still get its end, so the search runs a bit longer."""
    coordinator = _make_coordinator(hass)
    windows = []
    real_find_events = EarthSatellite.find_events

    def spy(satellite, observer, t0, t1, **kwargs):
        windows.append(t1.utc_datetime() - t0.utc_datetime())
        return real_find_events(satellite, observer, t0, t1, **kwargs)

    with patch.object(EarthSatellite, "find_events", spy):
        coordinator._calculate_next_pass(coordinator.satellite.epoch.utc_datetime())

    assert windows[0] == timedelta(hours=48) + PASS_SEARCH_MARGIN


async def test_update_during_a_real_pass_reports_duration_and_remaining(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """End to end, with nothing faked: in the middle of a pass the sensors' values follow from its rise and set."""
    coordinator = _make_coordinator(hass)
    (rise, culmination, set_time, _), _ = _two_passes(coordinator)
    now = culmination + (set_time - culmination) / 2  # past the peak, still above the horizon threshold
    freezer.move_to(now)
    coordinator.last_tle_attempt = now  # the TLE is current: no download
    coordinator.last_tle_fetch = now

    data = await coordinator._async_update_data()

    assert data["status"] == "Overhead Now!"
    assert abs(data["pass_duration"] - (set_time - rise).total_seconds()) < 2
    assert abs(data["pass_time_remaining"] - (set_time - now).total_seconds()) < 2
    assert 0 < data["pass_time_remaining"] < data["pass_duration"]
