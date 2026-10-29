"""Removing a satellite deletes its saved TLE cache and its repair issue."""
import re
from datetime import datetime, timezone

import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import (
    ISS_NORAD_ID,
    ISS_SATELLITE_NAME,
    ISS_TLE_LINE1,
    ISS_TLE_LINE2,
    TEST_NORAD_ID,
    TEST_SATELLITE_NAME,
    TEST_TLE_LINE1,
    TEST_TLE_LINE2,
)


@pytest.mark.parametrize(("norad_id", "title"), [(TEST_NORAD_ID, TEST_SATELLITE_NAME), (ISS_NORAD_ID, ISS_SATELLITE_NAME)])
async def test_removing_entry_deletes_cache_and_issue(hass: HomeAssistant, hass_storage, norad_id: str, title: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title=title, data={"norad_id": norad_id, "update_interval": 120})
    entry.add_to_hass(hass)
    await Store(hass, 1, f"{DOMAIN}_{norad_id}").async_save({
        "line1": ISS_TLE_LINE1,
        "line2": ISS_TLE_LINE2,
        "name": title,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    ir.async_create_issue(
        hass, DOMAIN, f"satellite_lost_{norad_id}", is_fixable=False, severity=ir.IssueSeverity.WARNING,
        translation_key="satellite_lost",
        translation_placeholders={"satellite_name": title, "satellite_id": norad_id},
    )
    assert f"{DOMAIN}_{norad_id}" in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}_{norad_id}" not in hass_storage
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"satellite_lost_{norad_id}") is None


async def test_removing_entry_with_leading_zero_deletes_legacy_cache(hass: HomeAssistant, hass_storage) -> None:
    """An entry saved as "025544" before normalization kept its cache under the raw ID."""
    entry = MockConfigEntry(domain=DOMAIN, title=ISS_SATELLITE_NAME, data={"norad_id": "025544", "update_interval": 120})
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}_025544"] = {"version": 1, "minor_version": 1, "key": f"{DOMAIN}_025544", "data": {}}
    hass_storage[f"{DOMAIN}_{ISS_NORAD_ID}"] = {"version": 1, "minor_version": 1, "key": f"{DOMAIN}_{ISS_NORAD_ID}", "data": {}}

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}_025544" not in hass_storage
    assert f"{DOMAIN}_{ISS_NORAD_ID}" not in hass_storage


async def test_removing_a_loaded_entry_deletes_what_setup_saved(hass: HomeAssistant, hass_storage) -> None:
    """The real sequence: set up (downloads and saves the TLE), then remove while loaded."""
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    with aioresponses() as m:
        m.get(
            re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"),
            status=200,
            body=f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n",
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert f"{DOMAIN}_{TEST_NORAD_ID}" in hass_storage  # saved by the first download

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert f"{DOMAIN}_{TEST_NORAD_ID}" not in hass_storage
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
