"""Test the Satellite Orbital Tracker coordinator and sensors."""
from datetime import timedelta
from unittest.mock import patch
from aioresponses import aioresponses
import pytest

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

    tle_text = (
        f"{TEST_SATELLITE_NAME}\n"
        f"{TEST_TLE_LINE1}\n"
        f"{TEST_TLE_LINE2}"
    )

    with aioresponses() as m:
        # Mock JSON format endpoint using fixture
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            payload=test_sample_tle_response,
            status=200,
        )
        # Mock TLE format endpoint
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=tle",
            body=tle_text,
            status=200,
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
        # We do NOT register any mock endpoint for CelesTrak here.
        # If the code attempts to fetch, aioresponses will raise an unhandled connection error.

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify successful setup without ever querying the external API
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    assert len(entries) > 0
