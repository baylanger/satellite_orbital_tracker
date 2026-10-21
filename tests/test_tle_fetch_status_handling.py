"""Tests for how non-200 CelesTrak responses affect the 404 (lost satellite) tracking."""
import re
from datetime import datetime, timedelta, timezone

from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TIMESCALE,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)

ISSUE_ID = f"satellite_lost_{TEST_NORAD_ID}"
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _make_coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)
    return SatelliteDataUpdateCoordinator(hass, config_entry, TEST_TIMESCALE)


def _create_lost_issue(hass: HomeAssistant) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="satellite_lost",
        translation_placeholders={"satellite_name": TEST_SATELLITE_NAME, "satellite_id": TEST_NORAD_ID},
    )


async def test_5xx_does_not_reset_404_streak(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A temporary 5xx outage in the middle of a 404 streak must not restart the 24-hour window."""
    freezer.move_to(T0)
    coordinator = _make_coordinator(hass)
    streak_start = T0 - timedelta(hours=2)
    coordinator.first_404_timestamp = streak_start

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=503)
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 503)
    assert coordinator.first_404_timestamp == streak_start


async def test_5xx_does_not_clear_existing_repair_issue(hass: HomeAssistant) -> None:
    """A 5xx response is not evidence that the satellite is back, so the repair issue stays."""
    coordinator = _make_coordinator(hass)
    _create_lost_issue(hass)
    issue_registry = ir.async_get(hass)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=500)
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 500)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_403_rate_limit_is_a_plain_failure(hass: HomeAssistant) -> None:
    """A 403 (rate limiting) fails the fetch without parsing the body or touching the 404 tracking."""
    coordinator = _make_coordinator(hass)
    _create_lost_issue(hass)

    with aioresponses() as m:
        m.get(
            coordinator.tle_url,
            status=403,
            # Even a body that looks like TLE data must be ignored for a non-200 status
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 403)
    assert coordinator.satellite is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_404_streak_survives_an_interleaved_5xx(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """404, then 503, then 404 more than 24 hours after the first one must still raise the repair issue."""
    freezer.move_to(T0)
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=404)
        await coordinator._async_fetch_and_parse_tle()
    assert coordinator.first_404_timestamp == T0

    freezer.move_to(T0 + timedelta(hours=1))
    with aioresponses() as m:
        m.get(coordinator.tle_url, status=503)
        await coordinator._async_fetch_and_parse_tle()
    assert coordinator.first_404_timestamp == T0

    freezer.move_to(T0 + timedelta(hours=25))
    with aioresponses() as m:
        m.get(coordinator.tle_url, status=404)
        await coordinator._async_fetch_and_parse_tle()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_successful_fetch_still_clears_streak_and_issue(hass: HomeAssistant) -> None:
    """Regression guard: a valid TLE response still resets the streak and removes the repair issue."""
    coordinator = _make_coordinator(hass)
    coordinator.first_404_timestamp = datetime.now(timezone.utc) - timedelta(hours=30)
    _create_lost_issue(hass)

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (True, 200)
    assert coordinator.first_404_timestamp is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
