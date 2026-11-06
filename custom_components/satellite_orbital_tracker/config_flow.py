"""Config flow for Satellite Orbital Tracker integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiohttp
import probatio

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.selector as selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, normalize_norad_id

class CannotConnect(Exception):
    """Error to indicate we cannot connect to the CelesTrak API endpoint."""

class CelesTrakRefused(Exception):
    """Error to indicate CelesTrak refused the request because the address asked too often."""


class InvalidNoradId(Exception):
    """Error to indicate the NORAD catalog ID is invalid or cannot be found."""


async def _async_download_tle(hass: HomeAssistant, norad_id: str) -> tuple[str, str, str]:
    """Download the TLE from CelesTrak and return (name, line 1, line 2).

    One request does it all: it proves that the NORAD ID exists and that CelesTrak can be reached, gives the
    satellite's name for the entry title, and the TLE itself is saved so that the setup of the new entry does
    not have to ask CelesTrak again.
    """
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle"
    headers = {"User-Agent": "HomeAssistant-SatelliteOrbitalTracker/2.0"}
    session = async_get_clientsession(hass)

    try:
        async with session.get(url, headers=headers, timeout=10) as response:
            # CelesTrak returns 404 status when a NORAD catalog ID does not exist
            if response.status == 404:
                raise InvalidNoradId
            # 403: CelesTrak limits how often an address may ask and refuses the request (its usage policy lists
            # 403 among the errors it sends before it blocks an address)
            if response.status == 403:
                raise CelesTrakRefused
            if response.status != 200:
                raise CannotConnect

            # An unknown catalog number can also be answered with HTTP 200 and a plain-text "No GP data found":
            # there are then no TLE lines, which is reported as an invalid NORAD ID.
            text = await response.text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            line1 = next((line for line in lines if line.startswith("1 ")), None)
            line2 = next((line for line in lines if line.startswith("2 ")), None)
            if not line1 and not line2:
                raise InvalidNoradId
            if not line1 or not line2:
                raise CannotConnect  # an incomplete answer is a bad connection, not a bad ID

            names = [line for line in lines if not line.startswith(("1 ", "2 ")) and len(line) > 2 and "---" not in line]
            return (names[0] if names else f"Satellite {norad_id}"), line1, line2

    except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
        raise CannotConnect
    except (InvalidNoradId, CannotConnect, CelesTrakRefused):
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
            norad_id = normalize_norad_id(user_input["norad_id"])

            # 1. A NORAD catalog number is digits only; anything else would also end up in the CelesTrak URL
            if norad_id is None:
                errors["norad_id"] = "norad_id_not_numeric"
            # 2. Prevent invalid non-integer float inputs (e.g. 4.5 seconds)
            elif isinstance(interval, float) and not interval.is_integer():
                errors["update_interval"] = "integer_required"
            # 3. Validate that update interval falls within the permitted bounds (5s to 3600s)
            elif not 5 <= int(interval) <= 3600:
                errors["update_interval"] = "invalid_interval"
            else:
                try:
                    # Download the TLE: validates existence/connectivity against CelesTrak and gives the name
                    satellite_name, line1, line2 = await _async_download_tle(self.hass, norad_id)
                except CelesTrakRefused:
                    errors["norad_id"] = "celestrak_refused"
                except CannotConnect:
                    errors["norad_id"] = "cannot_connect"
                except InvalidNoradId:
                    errors["norad_id"] = "invalid_norad_id"
                else:
                    # Set unique ID based on NORAD catalog number and abort if already configured
                    await self.async_set_unique_id(norad_id)
                    self._abort_if_unique_id_configured()

                    # Save the TLE like the coordinator does, so the new entry starts without another download
                    now = datetime.now(timezone.utc).isoformat()
                    await Store(self.hass, 1, f"{DOMAIN}_{norad_id}").async_save({
                        "line1": line1,
                        "line2": line2,
                        "name": satellite_name,
                        "timestamp": now,
                        "last_attempt": now,
                        "last_status": 200,
                    })

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
