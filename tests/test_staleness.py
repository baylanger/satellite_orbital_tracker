"""Tests for repeated calculation failures and TLE age visibility."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import (
    MAX_CONSECUTIVE_CALC_FAILURES,
    SatelliteDataUpdateCoordinator,
)

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, coordinator.ts)
    coordinator.last_tle_attempt = NOW
    coordinator.last_tle_fetch = NOW - timedelta(hours=3)
    return coordinator


async def _update(coordinator):
    with patch("custom_components.satellite_orbital_tracker.coordinator.datetime") as dt:
        dt.now.return_value = NOW
        dt.fromisoformat = datetime.fromisoformat
        return await coordinator._async_update_data()


async def test_data_exposes_tle_epoch_and_fetch_time(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    data = await _update(coordinator)
    assert data["tle_epoch"] == coordinator.satellite.epoch.utc_datetime().isoformat()
    assert data["tle_last_fetched"] == (NOW - timedelta(hours=3)).isoformat()


async def test_repeated_calc_failures_stop_serving_old_data(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = await _update(coordinator)

    with patch.object(coordinator.ts, "from_datetime", side_effect=RuntimeError("boom")):
        for _ in range(MAX_CONSECUTIVE_CALC_FAILURES - 1):
            assert await _update(coordinator) is coordinator.data
        with pytest.raises(UpdateFailed):
            await _update(coordinator)


async def test_success_resets_failure_counter(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = await _update(coordinator)
    with patch.object(coordinator.ts, "from_datetime", side_effect=RuntimeError("boom")):
        await _update(coordinator)
    assert coordinator.calc_failures == 1
    await _update(coordinator)
    assert coordinator.calc_failures == 0
