"""Config flow for Satellite Orbital Tracker integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, POPULAR_SATELLITES, DEFAULT_SATELLITE_ID

class SatelliteOrbitalTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Satellite Orbital Tracker."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            catnr = user_input["satellite_id"].strip()

            # Validate that the input is a valid numeric NORAD ID
            if not catnr.isdigit():
                errors["satellite_id"] = "invalid_catnr"
            else:
                sat_name = POPULAR_SATELLITES.get(catnr, f"Satellite {catnr}")

                await self.async_set_unique_id(catnr)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=sat_name,
                    data={
                        "satellite_id": catnr,
                        "satellite_name": sat_name,
                    },
                )

        # Allow selecting from popular dropdown or entering a custom ID
        schema = vol.Schema(
            {
                vol.Required("satellite_id", default=DEFAULT_SATELLITE_ID): str
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
