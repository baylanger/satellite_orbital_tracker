"""Tests for which update interval the coordinator polls with."""
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.satellite_orbital_tracker.const import DOMAIN
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME


def _coordinator(hass: HomeAssistant, data: dict, options: dict | None = None) -> SatelliteDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data=data, options=options or {})
    entry.add_to_hass(hass)
    return SatelliteDataUpdateCoordinator(hass, entry)


@pytest.mark.parametrize(
    ("data_interval", "options", "expected_seconds"),
    [
        (60, {}, 60),                      # chosen during setup, options flow never opened
        (60, {"update_interval": 300}, 300),  # a later change in the options flow wins
        (None, {}, 120),                   # nothing configured: default
        (None, {"update_interval": 45}, 45),
    ],
)
async def test_update_interval_source(
    hass: HomeAssistant, data_interval: int | None, options: dict, expected_seconds: int
) -> None:
    data = {"norad_id": TEST_NORAD_ID}
    if data_interval is not None:
        data["update_interval"] = data_interval

    coordinator = _coordinator(hass, data, options)

    assert coordinator.update_interval == timedelta(seconds=expected_seconds)
