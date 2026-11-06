"""Tests for the Current Elevation and Current Azimuth sensors."""
import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from skyfield.api import EarthSatellite

from custom_components.satellite_orbital_tracker.const import DOMAIN, compass_point
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator
from custom_components.satellite_orbital_tracker.sensor import SatelliteCurrentAzimuthSensor, SatelliteCurrentElevationSensor

from conftest import TEST_NORAD_ID, TEST_SATELLITE_NAME, TEST_TIMESCALE, TEST_TLE_LINE1, TEST_TLE_LINE2

CELESTRAK_BODY = f"{TEST_SATELLITE_NAME}\n{TEST_TLE_LINE1}\n{TEST_TLE_LINE2}\n"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=TEST_SATELLITE_NAME, data={"norad_id": TEST_NORAD_ID, "update_interval": 120})
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant) -> SatelliteDataUpdateCoordinator:
    coordinator = SatelliteDataUpdateCoordinator(hass, _entry(hass), TEST_TIMESCALE)
    coordinator.satellite = EarthSatellite(TEST_TLE_LINE1, TEST_TLE_LINE2, TEST_SATELLITE_NAME, TEST_TIMESCALE)
    return coordinator


async def test_azimuth_matches_skyfield_and_elevation(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    start = coordinator.satellite.epoch.utc_datetime()
    seen = set()
    for minutes in range(0, 1440, 17):
        t = TEST_TIMESCALE.from_datetime(start + timedelta(minutes=minutes))
        alt, az, _ = (coordinator.satellite - coordinator.home_observer).at(t).altaz()
        assert coordinator._calculate_current_azimuth(t) == pytest.approx(az.degrees)
        assert coordinator._calculate_current_altitude(t) == pytest.approx(alt.degrees)
        assert 0 <= coordinator._calculate_current_azimuth(t) < 360
        seen.add(alt.degrees >= 0)
    assert seen == {True, False}  # the sample covers both above and below the horizon


async def test_azimuth_is_unknown_on_error(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    coordinator.satellite = None
    assert coordinator._calculate_current_azimuth(TEST_TIMESCALE.now()) is None


async def test_update_reports_position(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    coordinator = _coordinator(hass)
    now = coordinator.satellite.epoch.utc_datetime()
    freezer.move_to(now)
    coordinator.last_tle_attempt = now
    coordinator.last_tle_fetch = now

    data = await coordinator._async_update_data()

    t = TEST_TIMESCALE.from_datetime(now)
    alt, az, _ = (coordinator.satellite - coordinator.home_observer).at(t).altaz()
    assert data["current_elevation_deg"] == pytest.approx(alt.degrees)
    assert data["current_azimuth_deg"] == pytest.approx(az.degrees)


async def test_sensor_values_and_direction(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    coordinator = SatelliteDataUpdateCoordinator(hass, entry, TEST_TIMESCALE)
    elevation, azimuth = SatelliteCurrentElevationSensor(coordinator, entry), SatelliteCurrentAzimuthSensor(coordinator, entry)

    coordinator.data = {"current_elevation_deg": -35.2, "current_azimuth_deg": 123.4}
    assert elevation.native_value == -35.2  # negative is kept
    assert azimuth.native_value == 123.4
    assert azimuth.extra_state_attributes == {"direction": compass_point(123.4)} == {"direction": "ESE"}

    for data in (None, {}, {"current_elevation_deg": float("nan"), "current_azimuth_deg": float("nan")}):
        coordinator.data = data
        assert elevation.native_value is None and azimuth.native_value is None
        assert azimuth.extra_state_attributes == {}


async def _setup(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with aioresponses() as m:
        m.get(re.compile(r"^https://celestrak\.org/NORAD/elements/gp\.php.*$"), status=200, body=CELESTRAK_BODY, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("object_id", ["current_elevation", "current_azimuth"])
async def test_entities_are_disabled_until_enabled_in_the_ui(hass: HomeAssistant, object_id: str) -> None:
    await _setup(hass)

    entity_id = f"sensor.satellite_css_tianhe_{object_id}"
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


async def test_entities_when_enabled(hass: HomeAssistant) -> None:
    with patch("homeassistant.helpers.entity.Entity.entity_registry_enabled_default", True):  # as if enabled in the UI
        await _setup(hass)

    elevation = hass.states.get("sensor.satellite_css_tianhe_current_elevation")
    azimuth = hass.states.get("sensor.satellite_css_tianhe_current_azimuth")
    assert elevation is not None and azimuth is not None
    assert -90 <= float(elevation.state) <= 90
    assert elevation.attributes["unit_of_measurement"] == "°"
    assert 0 <= float(azimuth.state) < 360
    assert azimuth.attributes["direction"] == compass_point(float(azimuth.state))
