"""Test the Satellite Orbital Tracker coordinator and sensors."""
from datetime import timedelta
from unittest.mock import patch
from aioresponses import aioresponses
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
