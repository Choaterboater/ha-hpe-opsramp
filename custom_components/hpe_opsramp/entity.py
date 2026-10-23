"""Shared entity helpers: the device for an OpsRamp tenant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TENANT_ID, DOMAIN, MANUFACTURER
from .coordinator import HpeOpsRampCoordinator


def account_uid(entry: ConfigEntry) -> str:
    """The tenant id prefixes every unique id."""
    return f"{entry.data[CONF_TENANT_ID]}_account"


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, account_uid(entry))},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="OpsRamp tenant",
        entry_type=DeviceEntryType.SERVICE,
    )


class OpsRampEntity(CoordinatorEntity[HpeOpsRampCoordinator]):
    _attr_has_entity_name = True
