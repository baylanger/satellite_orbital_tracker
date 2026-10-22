"""Tests for start-up behaviour: no reload after the first download, and the TLE schedule surviving restarts."""
import re
from datetime import timedelta
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
TLE_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"


def _celestrak_requests(m: aioresponses) -> int:
    """Number of requests sent to CelesTrak through the mock."""
    return sum(len(calls) for (_, url), calls in m.requests.items() if url.host == "celestrak.org")


def _add_entry(hass: HomeAssistant, title: str = TEST_SATELLITE_NAME) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=title, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


async def _save_cache(hass: HomeAssistant, age: timedelta, name: str = TEST_SATELLITE_NAME) -> None:
    """Store a cached TLE downloaded `age` ago (a negative age puts the timestamp in the future)."""
    await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": name,
        "timestamp": (dt_util.utcnow() - age).isoformat(),
    })


class _CoordinatorCounter:
    """Count how many coordinators are built, i.e. how many times the entry was set up."""

    def __init__(self) -> None:
        self.count = 0
        self._orig = SatelliteDataUpdateCoordinator.__init__

    def __enter__(self):
        counter = self

        def counting_init(coordinator, *args, **kwargs):
            counter.count += 1
            counter._orig(coordinator, *args, **kwargs)

        self._patch = patch.object(SatelliteDataUpdateCoordinator, "__init__", counting_init)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


async def test_new_entry_is_set_up_once_and_downloads_once(hass: HomeAssistant) -> None:
    """The first download must not trigger a reload (and a second download) of a new entry."""
    entry = _add_entry(hass)

    with aioresponses() as m, _CoordinatorCounter() as built:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert built.count == 1
        assert _celestrak_requests(m) == 1

    assert "satellite_name" not in entry.data


async def test_restart_with_fresh_cache_does_not_download(hass: HomeAssistant) -> None:
    """A TLE cached less than 12 hours ago is reused after a restart or reload instead of downloaded again."""
    await _save_cache(hass, timedelta(hours=1))
    entry = _add_entry(hass)

    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert _celestrak_requests(m) == 0


async def test_restart_with_stale_cache_downloads(hass: HomeAssistant) -> None:
    """A TLE cached more than 12 hours ago is refreshed at start-up."""
    await _save_cache(hass, timedelta(hours=13))
    entry = _add_entry(hass)

    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert _celestrak_requests(m) == 1


async def test_cache_timestamp_in_the_future_downloads(hass: HomeAssistant) -> None:
    """A cache timestamp in the future (clock moved back) must not postpone the download."""
    await _save_cache(hass, -timedelta(hours=1))
    entry = _add_entry(hass)

    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert _celestrak_requests(m) == 1


async def test_new_celestrak_name_updates_title_without_reload(hass: HomeAssistant) -> None:
    """When CelesTrak renames the satellite, the entry title follows but the entry is not reloaded."""
    await _save_cache(hass, timedelta(hours=13), name="OLD NAME")
    entry = _add_entry(hass, title="OLD NAME")

    with aioresponses() as m, _CoordinatorCounter() as built:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.title == TEST_SATELLITE_NAME
        assert built.count == 1


async def test_update_interval_change_reloads_without_downloading(hass: HomeAssistant) -> None:
    """Changing the interval in the options still reloads the entry, and the reload reuses the cached TLE."""
    entry = _add_entry(hass)

    with aioresponses() as m, _CoordinatorCounter() as built:
        m.get(CELESTRAK_URL, status=200, body=TLE_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(entry, options={"update_interval": 300})
        await hass.async_block_till_done()

        assert built.count == 2
        assert hass.data[DOMAIN][entry.entry_id].update_interval == timedelta(seconds=300)
        assert _celestrak_requests(m) == 1
