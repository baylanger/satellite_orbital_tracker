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

# Sample TLE data for mocking CelesTrak GP data format
SAMPLE_TLE_RESPONSE = [
    {
        "OBJECT_NAME": "ISS (ZARYA)",
        "NORAD_CAT_ID": 25544,
        "TLE_LINE1": "1 25544U 98067A   26010.50000000  .00016717  00000-0  10270-3 0  9993",
        "TLE_LINE2": "2 25544  51.6400 100.0000 0005000  0.0000 360.0000 15.50000000 12345",
    }
]

async def test_coordinator_and_sensors(hass: HomeAssistant) -> None:
    """Test setting up the config entry, coordinator updates, and sensor entities."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": "25544", "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    tle_text = (
        "ISS (ZARYA)\n"
        "1 25544U 98067A   26010.50000000  .00016717  00000-0  10270-3 0  9993\n"
        "2 25544  51.6400 100.0000 0005000  0.0000 360.0000 15.50000000 12345"
    )

    with aioresponses() as m:
        # Mock JSON format endpoint
        m.get(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json",
            payload=SAMPLE_TLE_RESPONSE,
            status=200,
        )
        # Mock TLE format endpoint if your coordinator fetches it directly
        m.get(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=tle",
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
    """Test coordinator behavior when the external API fails."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": "25544", "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        # Simulate network error or server drop
        m.get(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json",
            status=500,
        )

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
