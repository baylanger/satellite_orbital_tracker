"""Test the Satellite Orbital Tracker config flow."""
from unittest.mock import patch
from custom_components.satellite_orbital_tracker.const import DOMAIN
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import data_entry_flow, setup
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

import re

from conftest import (
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)

async def test_form_user(hass: HomeAssistant, test_sample_tle_response) -> None:
    """Test the user step form and successful entry creation with valid CelesTrak response."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    # Initialize the config flow session from user source
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {}

    # Mock CelesTrak JSON response for a valid satellite using TEST_NORAD_ID
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

        # Verify entry is successfully created with correct attributes and title
        assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result2["title"] == TEST_SATELLITE_NAME
        assert result2["data"]["norad_id"] == TEST_NORAD_ID
        assert result2["data"]["update_interval"] == 120


async def test_form_invalid_norad_id(hass: HomeAssistant) -> None:
    """Test handling of a non-existent NORAD catalog ID returning an HTTP 404 error."""
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

        # Verify form remains active and displays appropriate invalid ID error
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "invalid_norad_id"}

async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test handling of server connection failures (HTTP 500 error)."""
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

        # Verify form remains active and displays connection error
        assert result2["type"] == data_entry_flow.FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "cannot_connect"}

async def test_config_flow_options(hass: HomeAssistant) -> None:
    """Test options flow allows updating poll interval successfully post-setup."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}",
            repeat=True,
        )
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        # Setup now yields to the executor for the pass search, so check the final state after settling
        assert config_entry.state is ConfigEntryState.LOADED

        # Initialize options flow for the config entry
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        # Configure new update interval option
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"update_interval": 300},
        )
        assert result2["type"] == FlowResultType.CREATE_ENTRY
        assert config_entry.options.get("update_interval") == 300

        # Saving the options schedules a reload of the entry; let it finish while CelesTrak is still mocked
        # so no reload task or refresh timer is left running when the test ends.
        await hass.async_block_till_done()

async def test_config_flow_already_configured(hass: HomeAssistant) -> None:
    """Test config flow aborts if the satellite entry is already configured."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        unique_id=str(TEST_NORAD_ID),
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f'[{{"OBJECT_NAME": "{TEST_SATELLITE_NAME}"}}]',
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"norad_id": str(TEST_NORAD_ID), "update_interval": 120},
        )

        # Verify flow aborts due to existing unique identifier
        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "already_configured"

async def test_options_flow_invalid_interval(hass: HomeAssistant) -> None:
    """Test options flow rejects out-of-range update intervals via schema validation."""
    import probatio.error

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=TEST_SATELLITE_NAME,
        data={"norad_id": TEST_NORAD_ID, "update_interval": 120},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    # Depending on the Home Assistant version, invalid schema input surfaces either as
    # probatio's MultipleInvalid or as the flow manager's InvalidData wrapper.
    try:
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"update_interval": 5000},
        )
    except (probatio.error.MultipleInvalid, data_entry_flow.InvalidData) as err:
        assert "update_interval" in str(err)
    else:
        pytest.fail("Expected a schema validation error for out-of-range interval")

async def test_form_malformed_or_empty_celestrak_response(hass: HomeAssistant) -> None:
    """Test handling of 200 OK with empty brackets or malformed JSON payload from CelesTrak."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    with aioresponses() as m:
        # Mock a 200 OK response with an empty list [] which should trigger InvalidNoradId
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=200,
            body="[]",
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"norad_id": TEST_NORAD_ID, "update_interval": 120},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "invalid_norad_id"}

async def test_form_float_non_integer_interval(hass: HomeAssistant) -> None:
    """Test that a non-integer float update interval (e.g., 4.5) displays a validation error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"norad_id": TEST_NORAD_ID, "update_interval": 4.5},
    )

    # Verify integer_required error is returned for float inputs
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"update_interval": "integer_required"}

async def test_form_unexpected_parser_exception(hass: HomeAssistant) -> None:
    """Test that generic unexpected parsing exceptions trigger invalid_norad_id error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with aioresponses() as m:
        # Return invalid non-JSON body string to trigger parsing exception handling
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={TEST_NORAD_ID}&FORMAT=json",
            status=200,
            body="Not a valid json string...",
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"norad_id": TEST_NORAD_ID, "update_interval": 120},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["errors"] == {"norad_id": "invalid_norad_id"}
