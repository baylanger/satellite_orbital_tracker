"""Tests for caching of the next-pass search."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _make_coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    entry.add_to_hass(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry)
    coordinator.satellite = object()  # only identity matters for the cache key
    return coordinator


def _pass(minutes: int) -> dict:
    return {"time": (NOW + timedelta(minutes=minutes)).isoformat(), "event": "Rise (Above 10°)", "max_alt": 40.0}


async def test_result_reused_until_event_passes(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    with patch.object(coordinator, "_calculate_next_pass", return_value=_pass(30)) as calc:
        first = await coordinator._async_get_next_pass(NOW)
        second = await coordinator._async_get_next_pass(NOW + timedelta(minutes=10))
        assert first is second
        assert calc.call_count == 1

        await coordinator._async_get_next_pass(NOW + timedelta(minutes=31))
        assert calc.call_count == 2


async def test_recomputed_when_tle_changes(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    with patch.object(coordinator, "_calculate_next_pass", return_value=_pass(30)) as calc:
        await coordinator._async_get_next_pass(NOW)
        coordinator.satellite = object()  # new TLE fetched
        await coordinator._async_get_next_pass(NOW + timedelta(minutes=1))
        assert calc.call_count == 2


async def test_no_pass_result_rechecked_hourly(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    empty = {"time": None, "event": "Unknown", "max_alt": None}
    with patch.object(coordinator, "_calculate_next_pass", return_value=empty) as calc:
        await coordinator._async_get_next_pass(NOW)
        await coordinator._async_get_next_pass(NOW + timedelta(minutes=30))
        assert calc.call_count == 1
        await coordinator._async_get_next_pass(NOW + timedelta(minutes=61))
        assert calc.call_count == 2


async def test_search_runs_in_executor(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    with patch.object(hass, "async_add_executor_job", wraps=hass.async_add_executor_job) as job, patch.object(
        coordinator, "_calculate_next_pass", return_value=_pass(30)
    ):
        await coordinator._async_get_next_pass(NOW)
    job.assert_called_once()
