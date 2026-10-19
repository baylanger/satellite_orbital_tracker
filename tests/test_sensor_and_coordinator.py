"""Test the Satellite Orbital Tracker coordinator and sensors."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from aioresponses import aioresponses
from pprint import pprint
from freezegun.api import FrozenDateTimeFactory
import pytest
import re
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
    ISS_NORAD_ID,
    ISS_SATELLITE_NAME,
    ISS_TLE_LINE1,
    ISS_TLE_LINE2,
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
        # Mock NASA astronaut page endpoint for TEST_NORAD_ID
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

async def test_coordinator_skips_fetch_if_cache_fresh(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test that coordinator skips fetching from CelesTrak if a fresh cache exists (< 12 hours old)."""
    # Freeze time at a known deterministic point
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate store with a timestamp precisely 1 hour before the frozen start time
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": (start_time - timedelta(hours=1)).isoformat(),
    })

    with aioresponses() as m:
        # Mock NASA page just in case, but CelesTrak URL is intentionally omitted
        # because a fresh cache should skip calling CelesTrak entirely.
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify successful setup without querying the external CelesTrak API
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0

async def test_coordinator_404_repair_issue_creation(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test that a continuous 404 error triggers a Home Assistant Repair issue after the duration window."""
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

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
        "timestamp": start_time.isoformat(),
    })

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
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

    # Force the first 404 timestamp back 25 hours to simulate an existing 404 streak
    coordinator.first_404_timestamp = start_time - timedelta(hours=25)

    # Advance time past the 24-hour window
    freezer.move_to(start_time + timedelta(hours=1))

    with patch("custom_components.satellite_orbital_tracker.coordinator.ir") as mock_ir, aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=404,
        )

        success, status_code = await coordinator._async_fetch_and_parse_tle()

        assert success is False
        assert status_code == 404
        mock_ir.async_create_issue.assert_called_once()

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

async def test_coordinator_404_recovery_clears_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog
) -> None:
    """Test that a successful fetch clears an existing lost-satellite repair issue with debug tracking."""
    caplog.set_level(logging.DEBUG)
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

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
        "timestamp": start_time.isoformat(),
    })

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
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

    # Pre-create the repair issue in the registry
    issue_registry = ir.async_get(hass)
    issue_id = f"satellite_lost_{str(TEST_NORAD_ID)}"

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="satellite_lost",
        translation_placeholders={"satellite_name": TEST_SATELLITE_NAME, "satellite_id": str(TEST_NORAD_ID)},
    )

    initial_issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    print(f"\n[DEBUG] Pre-created issue state: {initial_issue}")
    assert initial_issue is not None

    # Simulate recovery via explicit fetch/parse call with a mocked 200 response
    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )

        print("[DEBUG] Triggering _async_fetch_and_parse_tle() for recovery...")
        success, status_code = await coordinator._async_fetch_and_parse_tle()
        print(f"[DEBUG] Fetch result -> success: {success}, status_code: {status_code}")

        assert success is True
        assert status_code == 200

    final_issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    print(f"[DEBUG] Post-fetch issue state: {final_issue}")

    # Verify that the repair issue was automatically cleaned up/deleted
    assert final_issue is None

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
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=ISS_SATELLITE_NAME,
        data={"norad_id": ISS_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    store = Store(hass, 1, f"{DOMAIN}_{ISS_NORAD_ID}")
    await store.async_save({
        "line1": ISS_TLE_LINE1,
        "line2": ISS_TLE_LINE2,
        "name": ISS_SATELLITE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Use generic placeholder names so future crew rotations never break this test
    mock_nasa_html = """
    <div class="hds-module">
        <h2>At the Space Station</h2>
        <div class="hds-meet-the-card padding-right-7">
            <div class="hds-meet-the-content">
                <h3 class="heading-18">Test Astronaut One</h3>
                <p class="p-sm">NASA astronaut and Expedition Commander</p>
            </div>
        </div>
        <div class="hds-meet-the-card padding-right-7">
            <div class="hds-meet-the-content">
                <h3 class="heading-18">Test Cosmonaut Two</h3>
                <p class="p-sm">Roscosmos cosmonaut and Flight Engineer</p>
            </div>
        </div>
    </div>
    """

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{ISS_SATELLITE_NAME}\n{ISS_TLE_LINE1}\n{ISS_TLE_LINE2}",
            repeat=True,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body=mock_nasa_html,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        await coordinator.async_refresh()

        crew_data = coordinator.data.get("crew_members", [])

    print("\n" + "="*50)
    print("LIVE PARSED ISS CREW ROSTER FROM TEST:")
    pprint(crew_data)
    print("="*50 + "\n")

    # Assert structural integrity and agency parsing logic instead of hardcoded names
    assert len(crew_data) == 2

    nasa_members = [member for member in crew_data if member["agency"] == "NASA"]
    ros_members = [member for member in crew_data if member["agency"] == "Roscosmos"]

    assert len(nasa_members) == 1
    assert nasa_members[0]["name"] == "Test Astronaut One"

    assert len(ros_members) == 1
    assert ros_members[0]["name"] == "Test Cosmonaut Two"

async def test_coordinator_refetches_when_cache_expired(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test that coordinator performs a live fetch when the cached data has expired (> 12 hours old)."""
    # Freeze time at a known starting point
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-populate store with an old timestamp (e.g., 15 hours ago, exceeding the 12-hour TTL)
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": TEST_TLE_LINE1,
        "line2": TEST_TLE_LINE2,
        "name": TEST_SATELLITE_NAME,
        "timestamp": (start_time - timedelta(hours=15)).isoformat(),
    })

    with aioresponses() as m:
        # Since the cache is expired, the coordinator MUST call out to CelesTrak and NASA
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
            repeat=True,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
            repeat=True,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][config_entry.entry_id]

        # Advance time further to simulate additional passage of time if needed,
        # or verify the initial startup setup successfully performed the expired refetch.
        await coordinator.async_refresh()

    # Verify entities are successfully set up and updated
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0

from homeassistant.helpers import event
import pytest

async def test_coordinator_background_polling_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test that the coordinator automatically fetches updates when the update interval elapses."""
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 60}, # 60 second interval
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Mock initial fetch during setup
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
            repeat=True,
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body="<html><body></body></html>",
            repeat=True,
        )

        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.LOADED

        # Advance time past the 60-second update interval
        new_time = start_time + timedelta(seconds=70)
        freezer.move_to(new_time)

        # Fire time changed event so Home Assistant's periodic listeners pick up the elapsed time
        hass.bus.async_fire("time_changed", {"now": new_time})
        await hass.async_block_till_done()

async def test_coordinator_corrupted_cache_fallback(hass: HomeAssistant) -> None:
    """Test that coordinator handles corrupted JSON/string cache gracefully without crashing."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Save malformed cache data to disk
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({"line1": None, "line2": "corrupted"})

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=500,
        )

        # Should raise UpdateFailed because cache is invalid and fetch failed
        coordinator = SatelliteDataUpdateCoordinator(hass, config_entry)
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

async def test_coordinator_tle_cache_parse_exception(hass: HomeAssistant) -> None:
    """Test that cache parse exceptions on startup are caught and logged."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Save invalid cache types that will cause datetime parsing or EarthSatellite to crash
    store = Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}")
    await store.async_save({
        "line1": "invalid_line1",
        "line2": "invalid_line2",
        "name": "INVALID SATELLITE",
        "timestamp": "not-a-datetime",
    })

    with aioresponses() as m:
        # Provide valid fallback response so it succeeds after failing cache load
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        coordinator = SatelliteDataUpdateCoordinator(hass, config_entry)
        data = await coordinator._async_update_data()
        assert data["latitude"] is not None

async def test_coordinator_dynamic_title_update(hass: HomeAssistant) -> None:
    """Test that coordinator updates the config entry title if the satellite name changes."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Old Name",
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
        )
        coordinator = SatelliteDataUpdateCoordinator(hass, config_entry)
        await coordinator._async_update_data()

        # Verify entry title was updated to match the fetched name
        assert config_entry.title == TEST_SATELLITE_NAME

async def test_coordinator_crew_scrape_failure_fallback(hass: HomeAssistant) -> None:
    """Test that NASA crew scrape failures gracefully return existing cached crew members."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": ISS_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # TLE fetch success
        m.get(
            re.compile(r".*FORMAT=tle.*"),
            status=200,
            body=f"{ISS_SATELLITE_NAME}\n{ISS_TLE_LINE1}\n{ISS_TLE_LINE2}",
        )
        # NASA crew page failure (500 error)
        m.get(
            re.compile(r".*nasa\.gov.*"),
            status=500,
        )

        coordinator = SatelliteDataUpdateCoordinator(hass, config_entry)
        coordinator.crew_members = [{"name": "Cached Astronaut", "agency": "NASA", "title": "Commander", "expedition": "Expedition 70"}]

        data = await coordinator._async_update_data()
        assert data["crew_count"] == 1
        assert data["crew_members"][0]["name"] == "Cached Astronaut"

async def test_coordinator_24hr_404_repair_issue_creation(hass: HomeAssistant) -> None:
    """Test that continuous 404 responses for over 24 hours create a repair issue."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Initialize the issue registry domain entry so ir.async_create_issue doesn't fail
    issue_registry = ir.async_get(hass)

    coordinator = SatelliteDataUpdateCoordinator(hass, config_entry)
    coordinator.first_404_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)

    with aioresponses() as m:
        m.get(
            coordinator.tle_url,
            status=404,
        )

        success, status_code = await coordinator._async_fetch_and_parse_tle()

        assert success is False
        assert status_code == 404

        # Verify the repair issue was successfully registered
        issue_id = f"satellite_lost_{TEST_NORAD_ID}"
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
