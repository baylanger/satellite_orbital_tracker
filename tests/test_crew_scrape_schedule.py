"""Tests for the NASA crew scrape schedule and parsing."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import (
    ISS_NORAD_ID,
    ISS_SATELLITE_NAME,
    ISS_TLE_LINE1,
    ISS_TLE_LINE2,
    TEST_TIMESCALE,
)

NASA_URL = "https://www.nasa.gov/humans-in-space/astronauts/"
T0 = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
CREW = [{"name": "A", "agency": "NASA", "title": "t", "expedition": None}]
HTML = """
<div class="hds-module"><h2>At the Space Station</h2>
<div class="hds-meet-the-card"><h3>Anil Menon</h3><p>NASA astronaut and Expedition 74/75 Flight Engineer</p></div>
</div>
"""


async def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title=ISS_SATELLITE_NAME, data={"norad_id": ISS_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    from skyfield.api import EarthSatellite

    coordinator.satellite = EarthSatellite(ISS_TLE_LINE1, ISS_TLE_LINE2, ISS_SATELLITE_NAME, coordinator.ts)
    coordinator.last_tle_attempt = T0
    coordinator.last_tle_fetch = T0
    return coordinator


async def _update(coordinator, when, scrape):
    with patch.object(coordinator, "_async_scrape_iss_crew", scrape), patch(
        "custom_components.satellite_orbital_tracker.coordinator.datetime"
    ) as dt:
        dt.now.return_value = when
        dt.fromisoformat = datetime.fromisoformat
        return await coordinator._async_update_data()


async def test_failed_scrape_not_retried_every_poll(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)
    scrape = AsyncMock(return_value=[])
    await _update(coordinator, T0, scrape)
    await _update(coordinator, T0 + timedelta(minutes=2), scrape)
    await _update(coordinator, T0 + timedelta(minutes=4), scrape)
    assert scrape.await_count == 1

    await _update(coordinator, T0 + timedelta(minutes=1441), scrape)
    assert scrape.await_count == 2


async def test_failed_refresh_keeps_cached_crew_and_retries_hourly(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)
    coordinator.crew_members = CREW
    coordinator.last_crew_fetch = T0 - timedelta(hours=25)
    coordinator.last_crew_attempt = T0 - timedelta(hours=25)
    scrape = AsyncMock(return_value=[])

    data = await _update(coordinator, T0, scrape)
    assert data["crew_members"] == CREW
    await _update(coordinator, T0 + timedelta(minutes=10), scrape)
    assert scrape.await_count == 1
    # A failure must not make the stale crew look freshly fetched
    assert coordinator.last_crew_fetch == T0 - timedelta(hours=25)


async def test_successful_scrape_waits_a_day(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)
    scrape = AsyncMock(return_value=CREW)
    await _update(coordinator, T0, scrape)
    await _update(coordinator, T0 + timedelta(hours=23), scrape)
    assert scrape.await_count == 1
    await _update(coordinator, T0 + timedelta(hours=25), scrape)
    assert scrape.await_count == 2


async def test_scrape_parses_html_and_http_error_returns_empty(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)
    with aioresponses() as m:
        m.get(NASA_URL, status=200, body=HTML)
        crew = await coordinator._async_scrape_iss_crew()
    assert [c["name"] for c in crew] == ["Anil Menon"]
    assert crew[0]["agency"] == "NASA" and crew[0]["expedition"] == "Expedition 74/75"

    with aioresponses() as m:
        m.get(NASA_URL, status=503, body="oops")
        assert await coordinator._async_scrape_iss_crew() == []


async def test_fetch_wrapper_falls_back_to_cached_crew(hass: HomeAssistant) -> None:
    coordinator = await _coordinator(hass)
    coordinator.crew_members = CREW
    with aioresponses() as m:
        m.get(NASA_URL, status=500)
        assert await coordinator._async_fetch_iss_crew_from_nasa() == CREW
