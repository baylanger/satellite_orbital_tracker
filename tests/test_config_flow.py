"""Test the Satellite Orbital Tracker config flow."""
from unittest.mock import patch
from homeassistant import data_entry_flow, setup
from homeassistant.core import HomeAssistant
from custom_components.satellite_orbital_tracker.const import DOMAIN
from aioresponses import aioresponses


async def test_form_user(hass: HomeAssistant) -> None:
    """Test the user step form and successful entry creation."""
    await setup.async_setup_component(hass, "persistent_notification", {})
    
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {}

    # Mock CelesTrak response for a valid satellite (e.g. ISS 25544)
    with aioresponses() as m:
        m.get(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json",
            payload=[{"OBJECT_NAME": "ISS (ZARYA)"}],
            status=200,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": "25544", "update_interval": 120},
        )
        
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["title"] == "ISS (ZARYA)"
        assert result2["data"]["norad_id"] == "25544"
        assert result2["data"]["update_interval"] == 120


async def test_form_invalid_norad_id(hass: HomeAssistant) -> None:
    """Test handling of a non-existent NORAD ID (HTTP 404)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with aioresponses() as m:
        m.get(
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=999999&FORMAT=json",
            status=404,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": "999999", "update_interval": 120},
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
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json",
            status=500,
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"norad_id": "25544", "update_interval": 120},
        )

        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "cannot_connect"}
