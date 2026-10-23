"""The alert_action action: acknowledge, suppress or close an OpsRamp alert. Refused for non-admin users
(automations and scripts may call it), and only works when the entry's options allow alert actions (off by default)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .api import OpsRampError, valid_alert_id
from .const import ALERT_ACTIONS, DOMAIN
from .coordinator import HpeOpsRampCoordinator

SERVICE_ALERT_ACTION = "alert_action"
ALERT_ACTION_SCHEMA = vol.Schema({
    vol.Required("alert_id"): cv.string,
    vol.Required("action"): vol.In(list(ALERT_ACTIONS)),
    vol.Optional("comment", default=""): cv.string,
    vol.Optional("config_entry_id"): cv.string,
})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    service.async_register_admin_service(hass, DOMAIN, SERVICE_ALERT_ACTION, _alert_action, schema=ALERT_ACTION_SCHEMA)


async def _alert_action(call: ServiceCall) -> None:
    alert_id = call.data["alert_id"].strip()
    if not valid_alert_id(alert_id):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_alert_id",
                                     translation_placeholders={"alert_id": alert_id})
    coordinator = _coordinator_for(call.hass, call.data.get("config_entry_id"), alert_id)
    if not coordinator.allow_actions:
        assert coordinator.config_entry
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="actions_disabled",
                                     translation_placeholders={"title": coordinator.config_entry.title})
    try:
        await coordinator.async_alert_action(alert_id, call.data["action"], call.data["comment"].strip())
    except OpsRampError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="action_failed",
                                 translation_placeholders={"error": str(err)}) from err


def _coordinator_for(hass: HomeAssistant, entry_id: str | None, alert_id: str) -> HpeOpsRampCoordinator:
    """The picked entry; else the one tenant that lists the alert; else the only tenant."""
    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id:
        loaded = [e for e in loaded if e.entry_id == entry_id]
    if not loaded:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
    holding = [e for e in loaded if alert_id in e.runtime_data.data.alerts]
    if len(holding) == 1:
        return holding[0].runtime_data
    if len(loaded) == 1:
        return loaded[0].runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="pick_entry")
