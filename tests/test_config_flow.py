"""Test the Satellite Orbital Tracker config flow."""
from unittest.mock import patch
from homeassistant import data_entry_flow, setup
from homeassistant.core import HomeAssistant
from custom_components.satellite_orbital_tracker.const import DOMAIN
from aioresponses import aioresponses

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
)


async def test_form_user(hass: HomeAssistant, test_sample_tle_response) -> None:
    """Test the user step form and successful entry creation."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {}

    # Mock CelesTrak response for a valid satellite using TEST_NORAD_ID
    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            payload=test_sample_tle_response,
            status=200,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": TEST_NORAD_ID, "update_interval": 120},
        )

        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["title"] == TEST_SATELLITE_NAME
        assert result2["data"]["norad_id"] == TEST_NORAD_ID
        assert result2["data"]["update_interval"] == 120


async def test_form_invalid_norad_id(hass: HomeAssistant) -> None:
    """Test handling of a non-existent NORAD ID (HTTP 404)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    invalid_id = "999999"
    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={invalid_id}&FORMAT=json",
            status=404,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": invalid_id, "update_interval": 120},
        )

        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "invalid_norad_id"}


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test handling of connection failures."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with aioresponses() as m:
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=500,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": TEST_NORAD_ID, "update_interval": 120},
        )

        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "cannot_connect"}
