"""Tests for NORAD ID validation and normalization in the config flow and the coordinator."""
import re
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN, normalize_norad_id
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import ISS_NORAD_ID, ISS_SATELLITE_NAME, TEST_TIMESCALE

CELESTRAK_URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("25544", "25544"),
        (" 025544 ", "25544"),
        (25544, "25544"),
        ("0", "0"),
        ("", None),
        ("ISS", None),
        ("-25544", None),
        ("25544&GROUP=stations", None),
        ("2554 4", None),
        ("²", None),  # a Unicode digit that int() would not take as "2"
    ],
)
def test_normalize_norad_id(value, expected) -> None:
    """Digits only, without leading zeros; anything else is rejected."""
    assert normalize_norad_id(value) == expected


@pytest.mark.parametrize("norad_id", ["ISS", "25544&GROUP=stations", "-1", ""])
async def test_form_rejects_non_numeric_id_without_calling_celestrak(hass: HomeAssistant, norad_id: str) -> None:
    """A non-numeric ID shows an error and is never put in a CelesTrak URL."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, payload=[{"OBJECT_NAME": ISS_SATELLITE_NAME}], repeat=True)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"norad_id": norad_id, "update_interval": 120}
        )
        assert not m.requests

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"norad_id": "norad_id_not_numeric"}


async def test_form_strips_leading_zeros(hass: HomeAssistant) -> None:
    """ "025544" is stored, queried and de-duplicated as "25544"."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    with aioresponses() as m, patch(
        "custom_components.satellite_orbital_tracker.async_setup_entry", return_value=True
    ):
        m.get(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={ISS_NORAD_ID}&FORMAT=json",
            status=200,
            payload=[{"OBJECT_NAME": ISS_SATELLITE_NAME}],
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"norad_id": " 025544 ", "update_interval": 120}
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["norad_id"] == ISS_NORAD_ID
    assert result2["result"].unique_id == ISS_NORAD_ID


async def test_form_leading_zero_duplicate_is_already_configured(hass: HomeAssistant) -> None:
    """The same satellite typed with a leading zero is recognised as already configured."""
    MockConfigEntry(
        domain=DOMAIN, title=ISS_SATELLITE_NAME, unique_id=ISS_NORAD_ID,
        data={"norad_id": ISS_NORAD_ID, "update_interval": 120},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with aioresponses() as m:
        m.get(CELESTRAK_URL, status=200, payload=[{"OBJECT_NAME": ISS_SATELLITE_NAME}])
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"norad_id": "025544", "update_interval": 120}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_coordinator_normalizes_existing_entry_with_leading_zero(hass: HomeAssistant) -> None:
    """An entry created before validation existed ("025544") is tracked as 25544, so the ISS crew applies."""
    entry = MockConfigEntry(domain=DOMAIN, title=ISS_SATELLITE_NAME, data={"norad_id": "025544", "update_interval": 120})
    entry.add_to_hass(hass)

    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)

    assert coordinator.satellite_id == ISS_NORAD_ID
    assert f"CATNR={ISS_NORAD_ID}&" in coordinator.tle_url
