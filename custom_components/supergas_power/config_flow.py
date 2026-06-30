from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_LOGISTIC_NUMBER,
    CONF_PHONE,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MIN_SCAN_INTERVAL_HOURS,
)
from .supergas_api import SupergasApiError, SupergasClient


class SupergasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            logistic = str(user_input[CONF_LOGISTIC_NUMBER]).strip()
            phone = str(user_input[CONF_PHONE]).strip()

            await self.async_set_unique_id(f"{DOMAIN}_{logistic}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = SupergasClient(session, logistic, phone)
            try:
                # Reachability only — no PII quota is spent, and a wrong phone
                # cannot be distinguished from the rate limit anyway.
                await client.async_check_reachable()
            except SupergasApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Supergas Power",
                    data={
                        CONF_LOGISTIC_NUMBER: logistic,
                        CONF_PHONE: phone,
                        CONF_SCAN_INTERVAL_HOURS: int(
                            user_input.get(
                                CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                            )
                        ),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOGISTIC_NUMBER): str,
                vol.Required(CONF_PHONE): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL_HOURS, default=DEFAULT_SCAN_INTERVAL_HOURS
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_HOURS)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SupergasOptionsFlow(config_entry)


class SupergasOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL_HOURS,
            self.config_entry.data.get(
                CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
            ),
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL_HOURS, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_HOURS)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
