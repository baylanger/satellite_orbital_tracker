"""Config flow for Satellite Orbital Tracker integration."""
from __future__ import annotations

import asyncio
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.selector as selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

class CannotConnect(Exception):
    """Error to indicate we cannot connect."""

class InvalidNoradId(Exception):
    """Error to indicate the NORAD ID is invalid or not found."""


async def _async_get_satellite_name(hass: HomeAssistant, norad_id: str) -> str:
    """Fetch the official satellite name from CelesTrak with validation."""
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=json"
    session = async_get_clientsession(hass)

    try:
        async with session.get(url, timeout=10) as response:
            # CelesTrak returns 404 when the NORAD ID doesn't exist
            if response.status == 404:
                raise InvalidNoradId
            if response.status != 200:
                raise CannotConnect

            data = await response.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                raise InvalidNoradId

            return data[0].get("OBJECT_NAME", f"Satellite {norad_id}").strip()

    except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
        raise CannotConnect
    except (InvalidNoradId, CannotConnect):
        raise
    except Exception:
        raise InvalidNoradId


class SatelliteOrbitalTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Satellite Orbital Tracker."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            interval = user_input.get("update_interval", 120)
            norad_id = str(user_input["norad_id"]).strip()

            # 1. Prevent decimals (e.g. 4.5)
            if isinstance(interval, float) and not interval.is_integer():
                errors["update_interval"] = "integer_required"
            # 2. Validate range
            elif not 5 <= int(interval) <= 3600:
                errors["update_interval"] = "invalid_interval"
            else:
                try:
                    # Attempt to fetch name and validate existence/connectivity
                    satellite_name = await _async_get_satellite_name(self.hass, norad_id)
                except CannotConnect:
                    errors["norad_id"] = "cannot_connect"
                except InvalidNoradId:
                    errors["norad_id"] = "invalid_norad_id"
                else:
                    user_input["update_interval"] = int(interval)
                    user_input["norad_id"] = norad_id

                    return self.async_create_entry(
                        title=satellite_name,
                        data=user_input,
                    )

        schema = vol.Schema(
            {
                vol.Required("norad_id"): str,
                vol.Required(
                    "update_interval", default=120
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        step=1,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> SatelliteOrbitalTrackerOptionsFlow:
        """Get the options flow for this handler."""
        return SatelliteOrbitalTrackerOptionsFlow()


class SatelliteOrbitalTrackerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Satellite Orbital Tracker."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            "update_interval",
            self.config_entry.data.get("update_interval", 120)
        )

        schema = vol.Schema(
            {
                vol.Required(
                    "update_interval",
                    default=current_interval,
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600))
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
