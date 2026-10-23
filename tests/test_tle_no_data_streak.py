"""Tests for HTTP 200 CelesTrak responses that contain no TLE data (unknown or decayed NORAD ID)."""
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
)

ISSUE_ID = f"satellite_lost_{TEST_NORAD_ID}"
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
NO_DATA_BODY = "No GP data found"


def _make_coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)
    return SatelliteDataUpdateCoordinator(hass, config_entry, TEST_TIMESCALE)


async def test_200_without_tle_starts_missing_streak(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A 200 response with no TLE lines is treated like a 404 and starts the streak."""
    freezer.move_to(T0)
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body=NO_DATA_BODY)
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 404)
    assert coordinator.first_404_timestamp == T0


async def test_200_without_tle_raises_repair_issue_after_24h(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A satellite answering 200 without data for over 24 hours raises the repair issue."""
    freezer.move_to(T0)
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body=NO_DATA_BODY)
        await coordinator._async_fetch_and_parse_tle()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None

    freezer.move_to(T0 + timedelta(hours=25))
    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body=NO_DATA_BODY)
        await coordinator._async_fetch_and_parse_tle()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_empty_body_counts_as_missing(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """An empty 200 body is also "no data"."""
    freezer.move_to(T0)
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body="")
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 404)
    assert coordinator.first_404_timestamp == T0


async def test_no_data_response_does_not_clear_existing_issue(hass: HomeAssistant) -> None:
    """A 200 with no TLE must not clear a repair issue raised earlier."""
    coordinator = _make_coordinator(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="satellite_lost",
        translation_placeholders={"satellite_name": TEST_SATELLITE_NAME, "satellite_id": TEST_NORAD_ID},
    )

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body=NO_DATA_BODY)
        await coordinator._async_fetch_and_parse_tle()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_partial_tle_is_a_plain_failure(hass: HomeAssistant) -> None:
    """Only one TLE line is a malformed response, not a missing satellite: the streak is left alone."""
    coordinator = _make_coordinator(hass)

    with aioresponses() as m:
        m.get(coordinator.tle_url, status=200, body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}")
        success, status_code = await coordinator._async_fetch_and_parse_tle()

    assert (success, status_code) == (False, 200)
    assert coordinator.first_404_timestamp is None
