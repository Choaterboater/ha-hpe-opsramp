"""On while the OpsRamp tenant has an open CRITICAL alert."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeOpsRampConfigEntry
from .coordinator import HpeOpsRampCoordinator
from .entity import OpsRampEntity, account_device_info, account_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeOpsRampConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([CriticalAlert(entry.runtime_data, entry)])


class CriticalAlert(OpsRampEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "critical_alert"

    def __init__(self, c: HpeOpsRampCoordinator, entry) -> None:
        super().__init__(c)
        self._attr_unique_id = f"{account_uid(entry)}_critical_alert"
        self._attr_device_info = account_device_info(entry)

    @property
    def is_on(self) -> bool:
        return any(a["state"] == "CRITICAL" for a in self.coordinator.data.alerts.values())
