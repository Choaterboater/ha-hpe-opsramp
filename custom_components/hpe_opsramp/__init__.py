"""HPE OpsRamp (unofficial): open alerts and incidents, active resources and Assist tools for an HPE OpsRamp tenant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import OpsRampClient
from .const import CONF_BASE_URL, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_TENANT_ID, DOMAIN
from .coordinator import HpeOpsRampCoordinator
from .llm_api import async_register_llm_api
from .services import async_setup_services
from .source import OpsRampSource

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HpeOpsRampConfigEntry = ConfigEntry[HpeOpsRampCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Once for all entries: the alert action and the Assist tools."""
    async_setup_services(hass)
    async_register_llm_api(hass)
    return True


def client_for(hass: HomeAssistant, data: dict[str, Any]) -> OpsRampClient:
    return OpsRampClient(async_get_clientsession(hass), data[CONF_BASE_URL], data[CONF_TENANT_ID],
                         data[CONF_CLIENT_ID], data[CONF_CLIENT_SECRET])


async def async_setup_entry(hass: HomeAssistant, entry: HpeOpsRampConfigEntry) -> bool:
    coordinator = HpeOpsRampCoordinator(hass, entry, OpsRampSource(client_for(hass, entry.data)))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpeOpsRampConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
