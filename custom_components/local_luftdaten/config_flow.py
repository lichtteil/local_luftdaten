"""Config flow for Local Luftdaten."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_RESOURCE,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DEFAULT_NAME,
    DEFAULT_RESOURCE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SENSOR_DESCRIPTIONS,
)

DEFAULT_MONITORED_CONDITIONS = ["SDS_P1", "SDS_P2", "signal"]


def _step_user_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return the user step schema."""
    default_scan_interval = int(DEFAULT_SCAN_INTERVAL.total_seconds())
    user_input = user_input or {}

    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)
            ): str,
            vol.Required(
                CONF_RESOURCE, default=user_input.get(CONF_RESOURCE, DEFAULT_RESOURCE)
            ): str,
            vol.Required(
                CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            ): bool,
            vol.Required(
                CONF_MONITORED_CONDITIONS,
                default=user_input.get(
                    CONF_MONITORED_CONDITIONS, DEFAULT_MONITORED_CONDITIONS
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(SENSOR_DESCRIPTIONS.keys()),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=user_input.get(CONF_SCAN_INTERVAL, default_scan_interval),
            ): vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )


class LocalLuftdatenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Local Luftdaten."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_step_user_schema(user_input),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        """Handle YAML import."""
        await self.async_set_unique_id(user_input[CONF_HOST])
        self._abort_if_unique_id_configured(updates=user_input)

        return self.async_create_entry(
            title=user_input[CONF_NAME],
            data=user_input,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get the options flow for this handler."""
        return LocalLuftdatenOptionsFlow()


class LocalLuftdatenOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Local Luftdaten."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_step_user_schema(current),
            errors={},
        )
