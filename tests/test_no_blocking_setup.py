"""Setting up the integration must not read Skyfield's data files on the event loop."""
import re
import threading
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import load

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)

async def test_timescale_is_built_off_the_event_loop(hass: HomeAssistant) -> None:
    """load.timescale() opens data files from disk, so it must run in the executor, not on the loop."""
    loop_thread = threading.get_ident()
    calling_threads: list[int] = []
    real_timescale = load.timescale

    def spy(*args, **kwargs):
        calling_threads.append(threading.get_ident())
        return real_timescale(*args, **kwargs)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    entry.add_to_hass(hass)

    with patch.object(load, "timescale", spy), aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
            repeat=True,
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert calling_threads, "the timescale was never built"
    assert loop_thread not in calling_threads
