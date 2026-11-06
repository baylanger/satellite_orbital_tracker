"""Tests for the translated entity names."""
import ast
import json
import re
from pathlib import Path

import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import patch

from custom_components.satellite_orbital_tracker.const import DOMAIN

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TLE_LINE1, TEST_TLE_LINE2

COMPONENT = Path(__file__).parent.parent / "custom_components" / "satellite_orbital_tracker"
LANGUAGES = ("en", "de", "es", "fr", "it", "nl")
CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"


def _translation_keys(filename: str) -> set[str]:
    return set(re.findall(r'_attr_translation_key = "([a-z_]+)"', (COMPONENT / filename).read_text()))


def _names(language: str, platform: str) -> dict[str, str]:
    data = json.loads((COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    return {key: value["name"] for key, value in data["entity"][platform].items()}


def test_entities_use_translation_keys_not_fixed_names() -> None:
    for filename in ("sensor.py", "binary_sensor.py"):
        tree = ast.parse((COMPONENT / filename).read_text())
        assigned = {t.attr for node in ast.walk(tree) if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Attribute)}
        assert "_attr_name" not in assigned, f"{filename} still sets a fixed English name"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(("filename", "platform"), [("sensor.py", "sensor"), ("binary_sensor.py", "binary_sensor")])
def test_every_entity_has_a_name_in_every_language(language: str, filename: str, platform: str) -> None:
    keys = _translation_keys(filename)
    names = _names(language, platform)
    assert keys
    assert set(names) == keys  # nothing missing and nothing left over
    assert all(name.strip() for name in names.values())


def test_english_names_are_the_original_ones() -> None:
    assert _names("en", "sensor")["next_visible_pass"] == "Next Visible Pass"
    assert _names("en", "binary_sensor")["visible_now"] == "Visible Now"
    assert len(_names("en", "sensor")) == 15 and len(_names("en", "binary_sensor")) == 2


@pytest.mark.parametrize("language", LANGUAGES)
def test_satellite_lost_message_says_24_hours(language: str) -> None:
    data = json.loads((COMPONENT / "translations" / f"{language}.json").read_text(encoding="utf-8"))
    assert "24" in data["issues"]["satellite_lost"]["description"]


async def _setup(hass: HomeAssistant) -> None:
    """Set the integration up with every entity enabled, as if they had been enabled in the UI."""
    with patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True):
        await _setup_integration(hass)


async def _setup_integration(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_english_entity_ids_and_names_are_unchanged(hass: HomeAssistant) -> None:
    await _setup(hass)

    state = hass.states.get("sensor.satellite_css_tianhe_status")
    assert state is not None
    assert state.name.endswith("Status")
    assert hass.states.get("binary_sensor.satellite_css_tianhe_visible_now") is not None
    assert hass.states.get("sensor.satellite_css_tianhe_next_visible_pass") is not None


async def test_names_follow_the_home_assistant_language(hass: HomeAssistant) -> None:
    hass.config.language = "fr"
    await _setup(hass)

    registry = er.async_get(hass)
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    names = {}
    for platform, unique_suffix in (("sensor", "status"), ("sensor", "next_visible_pass"), ("binary_sensor", "visible_now")):
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry_id}_{unique_suffix}")
        assert entity_id is not None
        names[unique_suffix] = hass.states.get(entity_id).name
    assert names["status"].endswith("État")
    assert names["next_visible_pass"].endswith("Prochain passage visible")
    assert names["visible_now"].endswith("Visible maintenant")


async def test_existing_entity_keeps_its_entity_id_when_names_become_translated(hass: HomeAssistant) -> None:
    """An entity registered before this change (same unique ID, explicit entity ID) keeps that ID in any language."""
    hass.config.language = "de"
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_status", config_entry=entry, suggested_object_id="satellite_css_tianhe_status"
    )
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status") == "sensor.satellite_css_tianhe_status"
    assert hass.states.get("sensor.satellite_css_tianhe_status").name.endswith("Status")
