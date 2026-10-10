"""Tests for the ISS crew scraping and sensor attribute exposure."""
from datetime import datetime, timezone
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry
from aioresponses import aioresponses

from custom_components.satellite_orbital_tracker.const import DOMAIN

async def test_iss_crew_sensor_attributes(hass: HomeAssistant) -> None:
    """Test that crew count and structured member dictionaries appear in sensor attributes."""
    iss_norad_id = 25544
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="ISS (ZARYA)",
        data={"norad_id": iss_norad_id, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    # Pre-seed store so TLE loads successfully from disk cache
    store = Store(hass, 1, f"{DOMAIN}_{iss_norad_id}")
    await store.async_save({
        "line1": "1 25544U 98067A   26080.50000000  .00016717  00000-0  30175-3 0  9999",
        "line2": "2 25544  51.6416 247.4627 0006703 130.5360 329.5445 15.49815350414522",
        "name": "ISS (ZARYA)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Mock NASA HTML block response matching the meet-the-card structure
    mock_nasa_html = """
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
    """

    with aioresponses() as m:
        # Mock CelesTrak TLE request
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={iss_norad_id}&FORMAT=tle",
            status=200,
            body="ISS (ZARYA)\n1 25544U...\n2 25544...",
        )
        # Mock NASA astronaut HTML page request
        m.get(
            "https://www.nasa.gov/humans-in-space/astronauts/",
            status=200,
            body=mock_nasa_html,
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    # Retrieve the registered sensor state entity
    state = hass.states.get("sensor.satellite_iss_zarya_status")
    assert state is not None

    # Verify attributes contain expected crew data structures
    attributes = state.attributes
    assert attributes.get("crew_count") == 2

    crew_members = attributes.get("crew_members")
    assert isinstance(crew_members, list)
    assert len(crew_members) == 2

    # Check first astronaut (NASA)
    assert crew_members[0]["name"] == "Anil Menon"
    assert crew_members[0]["agency"] == "NASA"
    assert crew_members[0]["expedition"] == "Expedition 74/75"

    # Check second astronaut (Roscosmos)
    assert crew_members[1]["name"] == "Pyotr Dubrov"
    assert crew_members[1]["agency"] == "Roscosmos"
    assert crew_members[1]["expedition"] == "Expedition 74/75"
