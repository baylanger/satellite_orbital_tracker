"""Pytest configuration for Satellite Orbital Tracker."""
import sys
from pathlib import Path
import pytest
from homeassistant.config_entries import ConfigEntryState
from skyfield.api import load

# Add the repository root directory to sys.path so custom_components can be imported correctly during test execution
sys.path.insert(0, str(Path(__file__).parent.parent))

# Enable custom integration loading plugin for pytest-homeassistant-custom-component
pytest_plugins = "pytest_homeassistant_custom_component"


def _patch_client_response_for_aioresponses() -> None:
    """Let aioresponses 0.7.9 build responses with aiohttp >= 3.14.

    aiohttp 3.14 made ``stream_writer`` a required keyword-only argument of
    ``ClientResponse.__init__``; aioresponses does not pass it yet, which made
    every mocked request raise TypeError. Supply a stub when it is missing.
    Remove once aioresponses releases support for aiohttp 3.14.
    """
    import inspect
    from unittest.mock import MagicMock

    from aiohttp import ClientResponse

    param = inspect.signature(ClientResponse.__init__).parameters.get("stream_writer")
    if param is None or param.default is not inspect.Parameter.empty:
        return
    original = ClientResponse.__init__

    def patched(self, *args, **kwargs):
        if "stream_writer" not in kwargs:
            kwargs["stream_writer"] = MagicMock(output_size=0)
        original(self, *args, **kwargs)

    ClientResponse.__init__ = patched


_patch_client_response_for_aioresponses()

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Automatically enable custom integrations for all tests in the suite."""
    yield

"""Common fixtures and test constants for Satellite Orbital Tracker."""
# Skyfield timescale shared by tests that build a coordinator directly (the integration loads it in the executor)
TEST_TIMESCALE = load.timescale()

# Centralized Test Constants representing CSS (TIANHE) test data
TEST_NORAD_ID = "48274"
TEST_SATELLITE_NAME = "CSS (TIANHE)"
TEST_TLE_LINE1 = "1 48274U 98067A   26010.50000000  .00016717  00000-0  10270-3 0  9993"
TEST_TLE_LINE2 = "2 48274  51.6400 100.0000 0005000  0.0000 360.0000 15.50000000 12345"
# ISS Constants representing ISS (ZARYA) data
ISS_NORAD_ID = "25544"
ISS_SATELLITE_NAME = "ISS (ZARYA)"
ISS_TLE_LINE1 = "1 25544U 98067A   26010.50000000  .00016717  00000-0  10270-3 0  9993"
ISS_TLE_LINE2 = "2 25544  51.6400 100.0000 0005000  0.0000 360.0000 15.50000000 12345"

@pytest.fixture(autouse=True)
async def unload_config_entries(hass):
    """Unload any config entry a test left loaded, so no refresh timer or task outlives the test."""
    yield
    for entry in hass.config_entries.async_entries("satellite_orbital_tracker"):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

@pytest.fixture
def test_sample_tle_response():
    """Fixture returning a mock CelesTrak JSON response payload for connectivity and config flow testing."""
    return [
        {
            "OBJECT_NAME": TEST_SATELLITE_NAME,
            "NORAD_CAT_ID": int(TEST_NORAD_ID),
            "TLE_LINE1": TEST_TLE_LINE1,
            "TLE_LINE2": TEST_TLE_LINE2,
        }
    ]
