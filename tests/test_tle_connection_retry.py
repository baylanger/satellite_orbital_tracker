"""Tests for the quick, quiet retries when CelesTrak cannot be reached (typically right after a restart)."""
import logging
import re
from datetime import datetime, timedelta, timezone

from aiohttp import ClientConnectionError
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    TLE_QUICK_RETRIES,
    TLE_QUICK_RETRY_INTERVAL,
    TLE_RETRY_INTERVAL,
    SatelliteDataUpdateCoordinator,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

TLE_URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
STEP = TLE_QUICK_RETRY_INTERVAL + timedelta(seconds=1)  # the retry is due once the interval has passed
HOUR = TLE_RETRY_INTERVAL + timedelta(seconds=1)
T0 = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)


def _coordinator_with_stale_cache(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> SatelliteDataUpdateCoordinator:
    """A coordinator as after a restart: a cached TLE older than 12 hours, so a download is due."""
    freezer.move_to(T0)
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    coordinator.last_tle_fetch = T0 - timedelta(hours=13)
    coordinator.last_tle_attempt = T0 - timedelta(hours=13)
    return coordinator


async def _poll(coordinator: SatelliteDataUpdateCoordinator, freezer: FrozenDateTimeFactory, after: timedelta, *, fail: bool) -> int:
    """Move time forward, run one update and return how many downloads it attempted."""
    freezer.tick(after)
    with aioresponses() as m:
        if fail:
            m.get(TLE_URL, exception=ClientConnectionError("network is not up yet"), repeat=True)
        else:
            m.get(TLE_URL, status=200, body=BODY, repeat=True)
        await coordinator._async_update_data()
        return sum(len(calls) for calls in m.requests.values())


async def test_connection_failure_is_quiet_and_retried_in_a_minute(hass, freezer, caplog) -> None:
    coordinator = _coordinator_with_stale_cache(hass, freezer)
    caplog.set_level(logging.DEBUG)

    assert await _poll(coordinator, freezer, timedelta(0), fail=True) == 1
    assert coordinator.last_fetch_status == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "TLE" in r.getMessage()]

    assert await _poll(coordinator, freezer, TLE_QUICK_RETRY_INTERVAL - timedelta(seconds=1), fail=True) == 0  # too early
    assert await _poll(coordinator, freezer, timedelta(seconds=2), fail=True) == 1  # a minute after the failure


async def test_retry_succeeds_and_resets(hass, freezer, caplog) -> None:
    coordinator = _coordinator_with_stale_cache(hass, freezer)
    await _poll(coordinator, freezer, timedelta(0), fail=True)

    assert await _poll(coordinator, freezer, STEP, fail=False) == 1

    assert coordinator.last_fetch_status == 200
    assert coordinator.connection_failures == 0
    assert coordinator.last_tle_fetch == T0 + STEP
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "TLE" in r.getMessage()]


async def test_lasting_failure_warns_and_goes_back_to_hourly(hass, freezer, caplog) -> None:
    coordinator = _coordinator_with_stale_cache(hass, freezer)
    caplog.set_level(logging.DEBUG)

    await _poll(coordinator, freezer, timedelta(0), fail=True)
    for _ in range(TLE_QUICK_RETRIES - 1):
        await _poll(coordinator, freezer, STEP, fail=True)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "TLE" in r.getMessage()]  # still quiet

    # The attempt after the last quick retry is the first that is reported
    await _poll(coordinator, freezer, STEP, fail=True)
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING and "TLE" in r.getMessage()]
    assert any("Failed to fetch fresh TLE" in r.getMessage() for r in loud)
    assert any("TLE fetch exception" in r.getMessage() for r in loud)

    # ... and from then on it waits an hour between attempts
    assert await _poll(coordinator, freezer, TLE_QUICK_RETRY_INTERVAL * 5, fail=True) == 0
    assert await _poll(coordinator, freezer, HOUR, fail=True) == 1


async def test_http_errors_still_use_the_hourly_retry(hass, freezer) -> None:
    coordinator = _coordinator_with_stale_cache(hass, freezer)
    freezer.tick(timedelta(0))
    with aioresponses() as m:
        m.get(TLE_URL, status=503, repeat=True)
        await coordinator._async_update_data()
    assert coordinator.last_fetch_status == 503
    assert coordinator.connection_failures == 0

    assert await _poll(coordinator, freezer, STEP, fail=True) == 0
    assert await _poll(coordinator, freezer, HOUR, fail=True) == 1


async def test_exception_log_names_the_exception_even_without_a_message(hass, freezer, caplog) -> None:
    """A timeout has an empty message, so the log must show the exception type to be of any use."""
    coordinator = _coordinator_with_stale_cache(hass, freezer)
    coordinator.connection_failures = TLE_QUICK_RETRIES  # past the quiet attempts, so it is logged as an error
    caplog.set_level(logging.DEBUG)

    freezer.tick(timedelta(0))
    with aioresponses() as m:
        m.get(TLE_URL, exception=TimeoutError(), repeat=True)
        await coordinator._async_update_data()

    assert any("TLE fetch exception" in r.getMessage() and "TimeoutError" in r.getMessage() for r in caplog.records)
