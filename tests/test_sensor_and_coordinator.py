"""Test the Satellite Orbital Tracker coordinator and sensors."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from aioresponses import aioresponses
from pprint import pprint
import pytest
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.satellite_orbital_tracker.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)

async def test_coordinator_and_sensors(hass: HomeAssistant, test_sample_tle_response) -> None:
    """Test setting up the config entry, coordinator updates, and sensor entities."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate the disk store cache so startup succeeds instantly
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with aioresponses() as m:
        # Mock NASA astronaut page endpoint for ISS (NORAD 25544)
        m.get(
            re.compile(r"^https://www\.nasa\.gov/humans-in-space/astronauts/.*$"),
            status=200,
            body="<html><body></body></html>",
            repeat=True,
        )
        # Mock CelesTrak just in case a background update triggers
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
            status=200,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify entities
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0

async def test_coordinator_update_failure(hass: HomeAssistant) -> None:
    """Test coordinator behavior when the external API fails completely with no cache."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Simulate network error or server drop on setup fetch
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=500,
        )
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=500,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

async def test_coordinator_server_error_failure(hass: HomeAssistant) -> None:
    """Test coordinator behavior when CelesTrak encounters a server error (5xx) with no cache."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Simulate a 5xx server-side outage
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=503,
        )
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=503,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_coordinator_invalid_satellite_404(hass: HomeAssistant) -> None:
    """Test coordinator behavior when a satellite NORAD ID no longer exists (404 Not Found)."""
    invalid_norad = "999999"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="NonExistent Satellite",
        data={"norad_id": invalid_norad, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Simulate CelesTrak returning a 404 for a retired or invalid NORAD ID
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={invalid_norad}&FORMAT=json",
            status=404,
        )
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={invalid_norad}&FORMAT=tle",
            status=404,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_coordinator_startup_fallback_to_cache(hass: HomeAssistant) -> None:
    """Test that coordinator successfully falls back to stored disk cache if CelesTrak fails on startup."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate the Home Assistant Store cache with valid TLE data
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        # Set timestamp to 2 days ago to force expiration (> 12 hours)
        "timestamp": (dt_util.utcnow() - timedelta(days=2)).isoformat(),
    })

    with aioresponses() as m:
        # Simulate CelesTrak being down/unreachable (500 error)
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=500,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        # Startup should still succeed because it successfully falls back to the disk cache
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify that entities are created and loaded using the cached TLE
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0

async def test_coordinator_skips_fetch_if_cache_fresh(hass: HomeAssistant) -> None:
    """Test that coordinator skips fetching from CelesTrak if a fresh cache exists (< 12 hours old)."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate with a very recent timestamp (1 hour ago)
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": (dt_util.utcnow() - timedelta(hours=1)).isoformat(),
    })

    with aioresponses() as m:
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify successful setup without ever querying the external API
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0

async def test_coordinator_404_repair_issue_creation(hass: HomeAssistant) -> None:
    """Test that a continuous 404 error triggers a Home Assistant Repair issue after the duration window."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Ensure timezone-aware timestamp for the 24-hour window
    coordinator.first_404_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)

    with patch("custom_components.satellite_orbital_tracker.coordinator.ir.async_create_issue") as mock_create_issue, aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=404,
        )

        success, status_code = await coordinator._async_fetch_and_parse_tle()

        assert success is False
        assert status_code == 404
        mock_create_issue.assert_called_once()

async def test_coordinator_5xx_server_error_retry_backoff(hass: HomeAssistant) -> None:
    """Test that 5xx server errors trigger retry backoff without creating a 404 repair issue."""
    from homeassistant.helpers import issue_registry as ir

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Simulate a 503 Service Unavailable error
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=503,
        )
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=503,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify that NO repair issue was created for a 5xx error (since it's a temporary outage, not a missing satellite)
    issue_registry = ir.async_get(hass)
    issue_id = f"satellite_lost_{TEST_NORAD_ID}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

async def test_coordinator_404_recovery_clears_issue(hass: HomeAssistant) -> None:
    """Test that a successful fetch clears an existing lost-satellite repair issue."""
    from homeassistant.helpers import issue_registry as ir

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Pre-create the repair issue using async_create_issue to simulate an active lost-satellite state
    issue_registry = ir.async_get(hass)
    issue_id = f"satellite_lost_{TEST_NORAD_ID}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="satellite_lost",
        translation_placeholders={"satellite_name": TEST_SATELLITE_NAME, "satellite_id": str(TEST_NORAD_ID)},
    )
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    # Simulate recovery: a successful 200 response comes in
    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )

        success, status_code = await coordinator._async_fetch_and_parse_tle()

        assert success is True
        assert status_code == 200

    # Verify that the repair issue was automatically cleaned up/deleted
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

async def test_coordinator_first_time_404_no_repair_issue(hass: HomeAssistant) -> None:
    """Test that a recent 404 error (within the 24-hour window) does not trigger a repair issue yet."""
    from homeassistant.helpers import issue_registry as ir

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
        )
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Leave coordinator.first_404_timestamp as None (brand new 404 streak)
    # OR set it to just 5 minutes ago (well inside the 24-hour window)
    coordinator.first_404_timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)

    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            status=404,
        )

        success, status_code = await coordinator._async_fetch_and_parse_tle()

        assert success is False
        assert status_code == 404

    # Verify that NO repair issue was created because the 24-hour window hasn't elapsed yet
    issue_registry = ir.async_get(hass)
    issue_id = f"satellite_lost_{TEST_NORAD_ID}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

async def test_coordinator_iss_structured_crew_scraping(hass: HomeAssistant) -> None:
    """Test that ISS crew scraping returns a valid roster under the 'At the Space Station' header."""
    iss_norad_id = 25544
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": iss_norad_id, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    store = Store(hass, 1, f"{DOMAIN}_{iss_norad_id}")
    await store.async_save({
        "line1": "1 25544U 98067A   26080.50000000  .00016717  00000-0  30175-3 0  9999",
        "line2": "2 25544  51.6416 247.4627 0006703 130.5360 329.5445 15.49815350414522",
        "name": "ISS (ZARYA)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    mock_nasa_html = """
    <div class="hds-module">
        <h2>At the Space Station</h2>
        <div class="hds-meet-the-card padding-right-7">
            <div class="hds-meet-the-content">
                <h3 class="heading-18">Jessica Watkins</h3>
                <p class="p-sm">NASA astronaut and Expedition 74/75 Commander</p>
            </div>
        </div>
        <div class="hds-meet-the-card padding-right-7">
            <div class="hds-meet-the-content">
                <h3 class="heading-18">Pyotr Dubrov</h3>
                <p class="p-sm">Roscosmos cosmonaut and Flight Engineer</p>
            </div>
        </div>
    </div>
    """

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body="ISS (ZARYA)\n1 25544U...\n2 25544...",
            repeat=True,
        )
        # Exact match pattern for NASA URL
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body=mock_nasa_html,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][config_entry.entry_id]

        # Trigger an explicit coordinator refresh INSIDE the aioresponses block
        await coordinator.async_refresh()

        # Extract data while still inside the mock scope to be fully safe
        crew_data = coordinator.data.get("crew_members", [])

    print("\n" + "="*50)
    print("LIVE PARSED ISS CREW ROSTER FROM TEST:")
    pprint(crew_data)
    print("="*50 + "\n")

    assert len(crew_data) > 0
    nasa_members = [member for member in crew_data if member["agency"] == "NASA"]
    assert len(nasa_members) > 0

async def test_coordinator_iss_structured_crew_scraping(hass: HomeAssistant) -> None:
    """Test that ISS crew scraping returns a valid roster with live debug output."""
    iss_norad_id = 25544
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": iss_norad_id, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate the store cache so setup initializes smoothly
    store = Store(hass, 1, f"{DOMAIN}_{iss_norad_id}")
    await store.async_save({
        "line1": "1 25544U 98067A   26080.50000000  .00016717  00000-0  30175-3 0  9999",
        "line2": "2 25544  51.6416 247.4627 0006703 130.5360 329.5445 15.49815350414522",
        "name": "ISS (ZARYA)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    with aioresponses() as m:
        # Mock CelesTrak endpoints persistently
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body="ISS (ZARYA)\n1 25544U...\n2 25544...",
            repeat=True,
        )
        # Mock NASA astronaut page with sample card markup
        m.get(
            re.compile(r"^https://www\.nasa\.gov/humans-in-space/astronauts/.*$"),
            status=200,
            body="""
            <div class="hds-meet-the-card padding-right-7">
                <div class="hds-meet-the-content">
                    <h3 class="heading-18">Jessica Watkins</h3>
                    <p class="p-sm">NASA astronaut and Expedition 74/75 Commander</p>
                </div>
            </div>
            <div class="hds-meet-the-card padding-right-7">
                <div class="hds-meet-the-content">
                    <h3 class="heading-18">Pyotr Dubrov</h3>
                    <p class="p-sm">Roscosmos cosmonaut and Flight Engineer</p>
                </div>
            </div>
            """,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    crew_data = await coordinator._async_fetch_iss_crew_from_nasa()

    # Print out the parsed crew roster to your terminal
    print("\n" + "="*50)
    print("LIVE PARSED ISS CREW ROSTER FROM TEST:")
    pprint(crew_data)
    print("="*50 + "\n")

    assert len(crew_data) > 0
    nasa_members = [member for member in crew_data if member["agency"] == "NASA"]
    assert len(nasa_members) > 0
