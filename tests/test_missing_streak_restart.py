"""The "satellite not found" streak must survive a restart, so the 24-hour count is not started again."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
ISSUE_ID = f"satellite_lost_{TEST_NORAD_ID}"
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
STORE_KEY = f"{DOMAIN}_{TEST_NORAD_ID}"


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)


async def _start_missing_streak(hass: HomeAssistant) -> None:
    """Simulate the first 404 of an outage at T0, using one coordinator that is then discarded (a restart)."""
    coordinator = _coordinator(hass)
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=404)
        await coordinator._async_fetch_and_parse_tle()


async def test_streak_start_is_saved(hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage) -> None:
    freezer.move_to(T0)
    await _start_missing_streak(hass)

    assert hass_storage[STORE_KEY]["data"]["missing_since"] == T0.isoformat()


async def test_restart_does_not_restart_the_24_hour_count(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    """Outage starts at T0; HA restarts 23 hours later; one more hour of 404s must raise the repair issue."""
    freezer.move_to(T0)
    await _start_missing_streak(hass)

    freezer.move_to(T0 + timedelta(hours=23))
    restarted = _coordinator(hass)
    assert restarted.first_404_timestamp is None  # nothing restored until the first update
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=404)
        with pytest.raises(UpdateFailed):  # no TLE is cached, so the update fails after the 404
            await restarted._async_update_data()
    assert restarted.first_404_timestamp == T0
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None

    freezer.move_to(T0 + timedelta(hours=25))
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=404)
        await restarted._async_fetch_and_parse_tle()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_successful_download_clears_the_saved_streak(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    freezer.move_to(T0)
    await _start_missing_streak(hass)

    freezer.move_to(T0 + timedelta(hours=2))
    coordinator = _coordinator(hass)
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n")
        success, _ = await coordinator._async_fetch_and_parse_tle()

    assert success
    assert coordinator.first_404_timestamp is None
    assert "missing_since" not in hass_storage[STORE_KEY]["data"]


async def test_future_streak_start_is_ignored(hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage) -> None:
    freezer.move_to(T0)
    hass_storage[STORE_KEY] = {
        "version": 1, "minor_version": 1, "key": STORE_KEY,
        "data": {"missing_since": (T0 + timedelta(days=3)).isoformat()},
    }
    coordinator = _coordinator(hass)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.first_404_timestamp is None


@pytest.mark.parametrize(
    ("saved", "expected_start"),
    [
        ("2026-10-11T12:00:00", T0),  # no time zone: read as UTC, so the streak continues from T0
        ("not a date", T0 + timedelta(hours=1)),  # unusable: ignored, this 404 starts a new streak
        (None, T0 + timedelta(hours=1)),
        (12345, T0 + timedelta(hours=1)),
    ],
)
async def test_saved_start_without_time_zone_or_damaged(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage, saved, expected_start: datetime
) -> None:
    """A damaged saved start must not raise; one without a time zone is read as UTC."""
    freezer.move_to(T0 + timedelta(hours=1))
    hass_storage[STORE_KEY] = {
        "version": 1, "minor_version": 1, "key": STORE_KEY, "data": {"missing_since": saved},
    }
    coordinator = _coordinator(hass)
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=404)
        with pytest.raises(UpdateFailed):  # no TLE is cached, so the update itself fails after the 404
            await coordinator._async_update_data()

    assert coordinator.first_404_timestamp == expected_start
