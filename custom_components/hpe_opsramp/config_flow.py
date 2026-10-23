"""Set up HPE OpsRamp in the UI: the tenant's API host and id and an OpsRamp custom integration's key and secret,
then how often to check and whether Home Assistant may act on alerts."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import client_for
from .api import OpsRampAuthError, OpsRampError, clean_base_url, valid_tenant_id
from .const import (
    CONF_ALLOW_ACTIONS,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SCAN,
    CONF_TENANT_ID,
    DEFAULT_ALLOW_ACTIONS,
    DOMAIN,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
    POLL_SECONDS,
)


class HpeOpsRampConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HpeOpsRampOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_BASE_URL: clean_base_url(user_input[CONF_BASE_URL]),
                CONF_TENANT_ID: user_input[CONF_TENANT_ID].strip(),
                CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
                CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET].strip(),
            }
            try:
                if not valid_tenant_id(data[CONF_TENANT_ID]):
                    errors[CONF_TENANT_ID] = "invalid_tenant"
                else:
                    await client_for(self.hass, data).check()
            except OpsRampAuthError:
                errors["base"] = "invalid_auth"
            except OpsRampError:
                errors["base"] = "cannot_connect"
            if not errors:
                await self.async_set_unique_id(data[CONF_TENANT_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="HPE OpsRamp",
                    data=data,
                    options={CONF_SCAN: POLL_SECONDS, CONF_ALLOW_ACTIONS: DEFAULT_ALLOW_ACTIONS},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL): str,
                    vol.Required(CONF_TENANT_ID): str,
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
            description_placeholders={"example_url": "https://acme.api.opsramp.com"},
        )


class HpeOpsRampOptionsFlow(OptionsFlow):
    """How often OpsRamp is checked, and whether alert actions are allowed (off by default)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(data={
                CONF_SCAN: int(user_input[CONF_SCAN]),
                CONF_ALLOW_ACTIONS: bool(user_input.get(CONF_ALLOW_ACTIONS, DEFAULT_ALLOW_ACTIONS)),
            })
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN, default=options.get(CONF_SCAN, POLL_SECONDS)): NumberSelector(
                        NumberSelectorConfig(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS, step=5,
                                             unit_of_measurement="s", mode=NumberSelectorMode.BOX)),
                    vol.Optional(
                        CONF_ALLOW_ACTIONS, default=options.get(CONF_ALLOW_ACTIONS, DEFAULT_ALLOW_ACTIONS)
                    ): BooleanSelector(),
                }
            ),
        )
