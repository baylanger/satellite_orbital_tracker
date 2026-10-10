"""Config flow for Satellite Orbital Tracker integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.selector as selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, POPULAR_SATELLITES, DEFAULT_SATELLITE_ID

async def _async_get_satellite_name(hass: HomeAssistant, norad_id: str) -> str:
    """Fetch the official satellite name from CelesTrak."""
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=json"
    session = async_get_clientsession(hass)

    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0].get("OBJECT_NAME", f"Satellite {norad_id}").strip()
    except Exception:
        pass

    return f"Satellite {norad_id}"


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
            # 2. Validate range using your custom translation key
            elif not 5 <= int(interval) <= 3600:
                errors["update_interval"] = "invalid_interval"
            else:
                # Save as a clean integer and update norad_id formatting
                user_input["update_interval"] = int(interval)
                user_input["norad_id"] = norad_id

                # Dynamically fetch the official name from CelesTrak for the entry title
                satellite_name = await _async_get_satellite_name(self.hass, norad_id)

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

        # self.config_entry is automatically provided by OptionsFlow base class
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
