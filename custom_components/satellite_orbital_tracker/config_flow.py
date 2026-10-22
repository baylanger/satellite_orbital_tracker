"""Config flow for Satellite Orbital Tracker integration."""
from __future__ import annotations

import asyncio
import aiohttp
import probatio

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.selector as selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

class CannotConnect(Exception):
    """Error to indicate we cannot connect to the CelesTrak API endpoint."""

class InvalidNoradId(Exception):
    """Error to indicate the NORAD catalog ID is invalid or cannot be found."""


async def _async_get_satellite_name(hass: HomeAssistant, norad_id: str) -> str:
    """Fetch the official satellite name from CelesTrak JSON endpoint with connection and existence validation."""
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=json"
    session = async_get_clientsession(hass)

    try:
        async with session.get(url, timeout=10) as response:
            # CelesTrak returns 404 status when a NORAD catalog ID does not exist
            if response.status == 404:
                raise InvalidNoradId
            if response.status != 200:
                raise CannotConnect

            # Do not insist on a JSON content type: CelesTrak can answer an unknown catalog number with HTTP 200
            # and a plain-text "No GP data found". That body then fails to parse and is reported as an invalid
            # NORAD ID below, instead of the ContentTypeError (an aiohttp.ClientError) showing "cannot connect".
            data = await response.json(content_type=None)
            if not data or not isinstance(data, list) or len(data) == 0:
                raise InvalidNoradId

            # Extract official object name or provide a fallback identifier string
            return data[0].get("OBJECT_NAME", f"Satellite {norad_id}").strip()

    except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
        raise CannotConnect
    except (InvalidNoradId, CannotConnect):
        raise
    except Exception:
        raise InvalidNoradId


class SatelliteOrbitalTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the configuration flow for adding a Satellite Orbital Tracker entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial user configuration step for NORAD ID and update interval."""
        errors = {}

        if user_input is not None:
            interval = user_input.get("update_interval", 120)
            norad_id = str(user_input["norad_id"]).strip()

            # 1. Prevent invalid non-integer float inputs (e.g. 4.5 seconds)
            if isinstance(interval, float) and not interval.is_integer():
                errors["update_interval"] = "integer_required"
            # 2. Validate that update interval falls within the permitted bounds (5s to 3600s)
            elif not 5 <= int(interval) <= 3600:
                errors["update_interval"] = "invalid_interval"
            else:
                try:
                    # Attempt to fetch name and validate existence/connectivity against CelesTrak
                    satellite_name = await _async_get_satellite_name(self.hass, norad_id)
                except CannotConnect:
                    errors["norad_id"] = "cannot_connect"
                except InvalidNoradId:
                    errors["norad_id"] = "invalid_norad_id"
                else:
                    # Set unique ID based on NORAD catalog number and abort if already configured
                    await self.async_set_unique_id(norad_id)
                    self._abort_if_unique_id_configured()

                    user_input["update_interval"] = int(interval)
                    user_input["norad_id"] = norad_id

                    return self.async_create_entry(
                        title=satellite_name,
                        data=user_input,
                    )

        # Define schema for the initial configuration form
        schema = probatio.Schema(
            {
                probatio.Required("norad_id"): str,
                probatio.Required(
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
        """Get the options flow handler for managing live interval adjustments."""
        return SatelliteOrbitalTrackerOptionsFlow()


class SatelliteOrbitalTrackerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow adjustments for Satellite Orbital Tracker instances."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Manage live configuration options like polling intervals."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Retrieve current interval from options or fallback to original config data
        current_interval = self.config_entry.options.get(
            "update_interval",
            self.config_entry.data.get("update_interval", 120)
        )

        schema = probatio.Schema(
            {
                probatio.Required(
                    "update_interval",
                    default=current_interval,
                ): probatio.All(probatio.Coerce(int), probatio.Range(min=5, max=3600))
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
