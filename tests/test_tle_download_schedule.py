"""TLE download schedule: 12 hours after a success, 6 hours after a failure (kept over restarts), and a short
delay after start-up so that Home Assistant can finish starting first."""
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import ClientConnectionError
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    TLE_FETCH_INTERVAL,
    TLE_RETRY_INTERVAL,
    TLE_STARTUP_DELAY,
    SatelliteDataUpdateCoordinator,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
STORE_KEY = f"{DOMAIN}_{TEST_NORAD_ID}"
OK = {"status": 200, "body": BODY}
FAIL = {"exception": ClientConnectionError("down")}


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    return SatelliteDataUpdateCoordinator(hass, _entry(hass), TEST_TIMESCALE)


async def _save(hass: HomeAssistant, *, fetched_ago: timedelta | None, attempt_ago: timedelta | None = None, status: int = 0) -> None:
    data = {}
    if fetched_ago is not None:
        data.update({"line1": TEST_TLE_LINE1, "line2": TEST_TLE_LINE2, "name": TEST_SATELLITE_NAME,
                     "timestamp": (T0 - fetched_ago).isoformat()})
    if attempt_ago is not None:
        data.update({"last_attempt": (T0 - attempt_ago).isoformat(), "last_status": status})
    await Store(hass, 1, STORE_KEY).async_save(data)


async def _update(coordinator: SatelliteDataUpdateCoordinator, freezer: FrozenDateTimeFactory, after: timedelta = timedelta(0), **mock) -> int:
    """Move time on, run one update with CelesTrak answering as given, and return how many downloads it made."""
    freezer.tick(after)
    with aioresponses() as m:
        m.get(URL, repeat=True, **mock)
        await coordinator._async_update_data()
        return sum(len(calls) for (_, url), calls in m.requests.items() if url.host == "celestrak.org")


async def test_failed_attempt_is_saved_and_waits_six_hours(hass, freezer) -> None:
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=13))
    coordinator = _coordinator(hass)

    assert await _update(coordinator, freezer, **FAIL) == 0  # start-up: the download is delayed
    assert await _update(coordinator, freezer, TLE_STARTUP_DELAY + timedelta(seconds=1), **FAIL) == 1  # tried, failed
    saved = await Store(hass, 1, STORE_KEY).async_load()
    assert saved["last_status"] == 0
    assert saved["line1"] == TEST_TLE_LINE1  # the saved TLE is kept

    assert await _update(coordinator, freezer, TLE_RETRY_INTERVAL - timedelta(minutes=1), **OK) == 0  # not within 6 hours
    assert await _update(coordinator, freezer, timedelta(minutes=2), **OK) == 1
    await coordinator.async_shutdown()


async def test_restart_does_not_skip_the_wait_after_a_failure(hass, freezer) -> None:
    """A failure one hour ago is remembered: the restart neither downloads at once nor a minute later."""
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=20), attempt_ago=timedelta(hours=1), status=0)
    coordinator = _coordinator(hass)

    assert await _update(coordinator, freezer, **OK) == 0
    assert coordinator._startup_timer is None  # nothing was scheduled
    assert await _update(coordinator, freezer, TLE_STARTUP_DELAY + timedelta(minutes=5), **OK) == 0
    assert await _update(coordinator, freezer, timedelta(hours=5), **OK) == 1  # 6 hours after the failure
    await coordinator.async_shutdown()


async def test_due_download_waits_a_minute_after_startup(hass: HomeAssistant) -> None:
    await _save(hass, fetched_ago=timedelta(hours=13))
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(URL, repeat=True, **OK)

        def requests() -> int:
            return sum(len(calls) for (_, url), calls in m.requests.items() if url.host == "celestrak.org")

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert requests() == 0
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
        assert requests() == 0
        async_fire_time_changed(hass, dt_util.utcnow() + TLE_STARTUP_DELAY + timedelta(seconds=30))
        await hass.async_block_till_done()
        assert requests() == 1
    assert (await Store(hass, 1, STORE_KEY).async_load())["last_status"] == 200


async def test_polling_during_the_delay_does_not_download(hass, freezer) -> None:
    """Pass updates every 5 seconds must not slip a download in before the delay is over."""
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=13))
    coordinator = _coordinator(hass)

    downloads = [await _update(coordinator, freezer, timedelta(seconds=5), **OK) for _ in range(10)]

    assert sum(downloads) == 0
    await coordinator.async_shutdown()


async def test_recent_cache_is_not_downloaded(hass, freezer) -> None:
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=3))
    coordinator = _coordinator(hass)

    assert await _update(coordinator, freezer, **OK) == 0
    assert await _update(coordinator, freezer, timedelta(minutes=10), **OK) == 0
    assert coordinator._startup_timer is None
    assert await _update(coordinator, freezer, TLE_FETCH_INTERVAL, **OK) == 1
    await coordinator.async_shutdown()


async def test_without_saved_tle_the_download_is_immediate(hass, freezer) -> None:
    freezer.move_to(T0)
    coordinator = _coordinator(hass)

    assert await _update(coordinator, freezer, **OK) == 1
    await coordinator.async_shutdown()


async def test_first_download_failure_without_saved_tle_waits_six_hours(hass, freezer) -> None:
    freezer.move_to(T0)
    coordinator = _coordinator(hass)

    with pytest.raises(UpdateFailed, match="Next attempt in about 6 hours"):
        await _update(coordinator, freezer, **FAIL)
    with pytest.raises(UpdateFailed, match="next attempt"):  # Home Assistant's own setup retries do not ask again
        await _update(coordinator, freezer, timedelta(minutes=1), **OK)
    assert (await Store(hass, 1, STORE_KEY).async_load())["last_status"] == 0

    assert await _update(coordinator, freezer, TLE_RETRY_INTERVAL, **OK) == 1
    await coordinator.async_shutdown()


async def test_http_errors_use_the_same_six_hour_wait(hass, freezer) -> None:
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=13))
    coordinator = _coordinator(hass)

    await _update(coordinator, freezer, status=403)
    assert await _update(coordinator, freezer, TLE_STARTUP_DELAY + timedelta(seconds=1), status=403) == 1
    assert coordinator.last_fetch_status == 403
    assert await _update(coordinator, freezer, timedelta(hours=5, minutes=50), status=403) == 0
    assert await _update(coordinator, freezer, timedelta(minutes=20), status=403) == 1
    await coordinator.async_shutdown()


async def test_log_names_the_cause_and_the_next_attempt(hass, freezer, caplog) -> None:
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=13))
    coordinator = _coordinator(hass)

    await _update(coordinator, freezer, exception=TimeoutError())  # a timeout has an empty message
    with caplog.at_level(logging.WARNING):
        await _update(coordinator, freezer, TLE_STARTUP_DELAY + timedelta(seconds=1), exception=TimeoutError())
    await coordinator.async_shutdown()

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 1
    assert "TimeoutError" in messages[0]
    assert "Next attempt in about 6 hours" in messages[0]
    expected = dt_util.as_local(T0 + TLE_STARTUP_DELAY + timedelta(seconds=1) + TLE_RETRY_INTERVAL)
    assert f"{expected:%Y-%m-%d %H:%M}" in messages[0]


async def test_file_saved_by_an_older_version_follows_the_last_download(hass, freezer) -> None:
    """A saved file without attempt information is scheduled from the last successful download."""
    freezer.move_to(T0)
    await _save(hass, fetched_ago=timedelta(hours=3))
    coordinator = _coordinator(hass)

    assert await _update(coordinator, freezer, **OK) == 0
    assert coordinator.last_tle_attempt == T0 - timedelta(hours=3)
