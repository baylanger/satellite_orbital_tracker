"""The entry title follows CelesTrak's name only while the integration chose it, never over a user's title."""
from datetime import datetime, timedelta, timezone
import re

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
NASA_URL = "https://www.nasa.gov/humans-in-space/astronauts/"
CSS_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"


async def _setup_with_download(hass: HomeAssistant, title: str, cached_name: str | None = None) -> MockConfigEntry:
    """Set up an entry whose TLE is downloaded now (no cache, or a cache older than 12 hours named cached_name)."""
    if cached_name is not None:
        await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_save({
            "line1": TEST_TLE_LINE1, "line2": TEST_TLE_LINE2, "name": cached_name,
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat(),
        })
    entry = MockConfigEntry(domain=DOMAIN, title=title, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, body=CSS_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_user_title_survives_the_first_download(hass: HomeAssistant) -> None:
    """A title the user typed before the first download is kept, not replaced by CelesTrak's name."""
    entry = await _setup_with_download(hass, title="My tracker")

    assert entry.title == "My tracker"


async def test_default_title_becomes_celestrak_name(hass: HomeAssistant) -> None:
    """The title chosen by the integration (here, the placeholder) follows CelesTrak on the first download."""
    entry = await _setup_with_download(hass, title=f"Satellite {TEST_NORAD_ID}")

    assert entry.title == TEST_SATELLITE_NAME


async def test_previous_celestrak_name_follows_a_rename(hass: HomeAssistant) -> None:
    """A title equal to the name saved last time is updated when CelesTrak renames the satellite."""
    entry = await _setup_with_download(hass, title="OLD NAME", cached_name="OLD NAME")

    assert entry.title == TEST_SATELLITE_NAME


async def test_user_title_survives_a_celestrak_rename(hass: HomeAssistant) -> None:
    entry = await _setup_with_download(hass, title="My tracker", cached_name="OLD NAME")

    assert entry.title == "My tracker"
