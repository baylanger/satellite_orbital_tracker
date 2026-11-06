"""The config flow downloads the TLE once: it names the satellite, is saved, and the new entry reuses it."""
import json
import re
from pathlib import Path

import pytest
from aiohttp import ClientConnectionError
from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

URL = re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$")
BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"
TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "satellite_orbital_tracker" / "translations"


def _requests(m: aioresponses) -> int:
    return sum(len(calls) for (_, url), calls in m.requests.items() if url.host == "celestrak.org")


async def _submit(hass: HomeAssistant, m: aioresponses) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"norad_id": TEST_NORAD_ID, "update_interval": 120})


async def test_adding_a_satellite_makes_one_request_and_saves_the_tle(hass: HomeAssistant) -> None:
    with aioresponses() as m:
        m.get(URL, status=200, body=BODY, repeat=True)
        result = await _submit(hass, m)
        await hass.async_block_till_done()  # the new entry is set up

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == TEST_SATELLITE_NAME
        assert _requests(m) == 1  # the setup of the entry reused the saved TLE

    saved = await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_load()
    assert saved["line1"] == TEST_TLE_LINE1 and saved["line2"] == TEST_TLE_LINE2
    assert saved["name"] == TEST_SATELLITE_NAME
    assert saved["last_status"] == 200 and saved["timestamp"] == saved["last_attempt"]
    coordinator = hass.data[DOMAIN][result["result"].entry_id]
    assert coordinator.satellite is not None


async def test_refused_request_has_its_own_error(hass: HomeAssistant) -> None:
    with aioresponses() as m:
        m.get(URL, status=403)
        result = await _submit(hass, m)

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"norad_id": "celestrak_refused"}
    assert await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_load() is None


@pytest.mark.parametrize("answer", [{"status": 500}, {"status": 503}, {"status": 429}, {"exception": TimeoutError()},
                                    {"exception": ClientConnectionError("down")}])
async def test_other_failures_are_connection_errors(hass: HomeAssistant, answer: dict) -> None:
    with aioresponses() as m:
        m.get(URL, **answer)
        result = await _submit(hass, m)

    assert result["errors"] == {"norad_id": "cannot_connect"}
    assert await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_load() is None


@pytest.mark.parametrize(("answer", "error"), [
    ({"status": 404}, "invalid_norad_id"),
    ({"status": 200, "body": "No GP data found"}, "invalid_norad_id"),
    ({"status": 200, "body": TEST_TLE_LINE1}, "cannot_connect"),  # an incomplete answer
])
async def test_unknown_id_and_incomplete_answers(hass: HomeAssistant, answer: dict, error: str) -> None:
    with aioresponses() as m:
        m.get(URL, **answer)
        result = await _submit(hass, m)

    assert result["errors"] == {"norad_id": error}
    assert await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_load() is None


async def test_without_a_name_line_the_placeholder_is_used(hass: HomeAssistant) -> None:
    with aioresponses() as m:
        m.get(URL, status=200, body=f"{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n", repeat=True)
        result = await _submit(hass, m)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Satellite {TEST_NORAD_ID}"


async def test_already_configured_satellite_keeps_its_saved_tle(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, title="Mine", unique_id=TEST_NORAD_ID,
                    data={"norad_id": TEST_NORAD_ID, "update_interval": 120}).add_to_hass(hass)
    await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_save({"name": "kept"})

    with aioresponses() as m:
        m.get(URL, status=200, body=BODY)
        result = await _submit(hass, m)

    assert result["type"] == FlowResultType.ABORT
    assert (await Store(hass, 1, f"{DOMAIN}_{TEST_NORAD_ID}").async_load()) == {"name": "kept"}


@pytest.mark.parametrize("language", ["en", "de", "es", "fr", "it", "nl"])
def test_every_language_has_the_error_texts(language: str) -> None:
    english = json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))["config"]["error"]
    errors = json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))["config"]["error"]
    assert set(errors) == set(english)
    assert "celestrak_refused" in errors and errors["celestrak_refused"].strip()
    assert errors["celestrak_refused"] != errors["cannot_connect"]
