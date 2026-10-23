"""Tests for the ISS crew scraping and sensor attribute exposure."""
from datetime import datetime, timezone
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
import re

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import (
    ISS_NORAD_ID,
    ISS_SATELLITE_NAME,
    ISS_TLE_LINE1,
    ISS_TLE_LINE2,
)

async def test_iss_crew_sensor_attributes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test that crew count and structured member dictionaries appear correctly in sensor state attributes."""
    # Freeze time to a deterministic starting timestamp for consistency
    start_time = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    freezer.move_to(start_time)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=ISS_SATELLITE_NAME,
        data={"norad_id": ISS_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-seed Home Assistant persistent storage store with valid TLE cache matching frozen timestamp
    store = Store(hass, 1, f"{DOMAIN}_{ISS_NORAD_ID}")
    await store.async_save({
        "line1": ISS_TLE_LINE1,
        "line2": ISS_TLE_LINE2,
        "name": ISS_SATELLITE_NAME,
        "timestamp": start_time.isoformat(),
    })

    # Mock HTML structure mirroring NASA's astronaut page format for crew parsing tests
    mock_nasa_html = """
    <div class="hds-module">
    <h2>At the Space Station</h2>
    <div class="hds-meet-the-card padding-right-7">
        <div class="hds-meet-the-content">
            <h3 class="heading-18">Anil Menon</h3>
            <p class="p-sm">NASA astronaut and Expedition 74/75 Flight Engineer</p>
        </div>
    </div>
    <div class="hds-meet-the-card padding-right-7">
        <div class="hds-meet-the-content">
            <h3 class="heading-18">Pyotr Dubrov</h3>
            <p class="p-sm">Roscosmos cosmonaut and Expedition 74/75 Flight Engineer</p>
        </div>
    </div>
    </div>
    """

    # Mock network endpoints for both CelesTrak TLE fetching and NASA astronaut scraping
    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{ISS_SATELLITE_NAME}\n{ISS_TLE_LINE1}\n{ISS_TLE_LINE2}",
        )
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body=mock_nasa_html,
        )

        # Set up integration entry and wait for background tasks to complete
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Retrieve the registered main status sensor entity state from Home Assistant state machine
    state = hass.states.get("sensor.satellite_iss_zarya_status")
    assert state is not None

    # Verify attributes contain expected crew count and parsed data structures
    attributes = state.attributes
    assert attributes.get("crew_count") == 2

    crew_members = attributes.get("crew_members")
    assert isinstance(crew_members, list)
    assert len(crew_members) == 2

    # Validate first astronaut (NASA) attributes and expedition parsing
    assert crew_members[0]["name"] == "Anil Menon"
    assert crew_members[0]["agency"] == "NASA"
    assert crew_members[0]["expedition"] == "Expedition 74/75"

    # Validate second astronaut (Roscosmos) attributes and expedition parsing
    assert crew_members[1]["name"] == "Pyotr Dubrov"
    assert crew_members[1]["agency"] == "Roscosmos"
    assert crew_members[1]["expedition"] == "Expedition 74/75"
