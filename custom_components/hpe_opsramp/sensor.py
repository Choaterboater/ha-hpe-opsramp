"""Sensors for an OpsRamp tenant: open alerts (all, critical, warning, not acknowledged), open incidents, active resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeOpsRampConfigEntry
from .const import MAX_LIST_ROWS
from .coordinator import HpeOpsRampCoordinator, sorted_alerts, unacknowledged
from .entity import OpsRampEntity, account_device_info, account_uid
from .source import Alert, Incident, Snapshot

# List-shaped attributes stay out of the recorder.
_LIST_ATTRS = frozenset({"alerts", "incidents"})
_MEASURE = SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class OpsRampSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Snapshot], Any]
    attrs_fn: Callable[[Snapshot], dict[str, Any]] | None = None


def alert_row(a: Alert) -> dict[str, Any]:
    return {k: a.get(k) for k in ("id", "state", "subject", "resource", "priority", "acknowledged", "created")}


def _incident_rows(incidents: list[Incident]) -> list[dict[str, Any]]:
    """Newest first."""
    return sorted(incidents, key=lambda i: i.get("created") or "", reverse=True)[:MAX_LIST_ROWS]


def _count(d: Snapshot, state: str) -> int:
    return sum(1 for a in d.alerts.values() if a["state"] == state)


SENSORS = (
    OpsRampSensorDescription(
        key="open_alerts", translation_key="open_alerts", state_class=_MEASURE,
        value_fn=lambda d: len(d.alerts),
        attrs_fn=lambda d: {"alerts": [alert_row(a) for a in sorted_alerts(d.alerts.values())][:MAX_LIST_ROWS]},
    ),
    OpsRampSensorDescription(
        key="critical_alerts", translation_key="critical_alerts", state_class=_MEASURE,
        value_fn=lambda d: _count(d, "CRITICAL"),
    ),
    OpsRampSensorDescription(
        key="warning_alerts", translation_key="warning_alerts", state_class=_MEASURE,
        value_fn=lambda d: _count(d, "WARNING"),
    ),
    OpsRampSensorDescription(
        key="unacknowledged_alerts", translation_key="unacknowledged_alerts", state_class=_MEASURE,
        value_fn=lambda d: sum(1 for a in d.alerts.values() if unacknowledged(a)),
    ),
    OpsRampSensorDescription(
        key="open_incidents", translation_key="open_incidents", state_class=_MEASURE,
        value_fn=lambda d: None if d.incidents is None else len(d.incidents),
        attrs_fn=lambda d: {"incidents": _incident_rows(d.incidents or [])},
    ),
    OpsRampSensorDescription(
        key="resources", translation_key="resources", state_class=_MEASURE,
        value_fn=lambda d: d.resources,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeOpsRampConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(AccountSensor(entry.runtime_data, entry, d) for d in SENSORS)


class AccountSensor(OpsRampEntity, SensorEntity):
    entity_description: OpsRampSensorDescription
    _unrecorded_attributes = _LIST_ATTRS

    def __init__(self, c: HpeOpsRampCoordinator, entry, description: OpsRampSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._attr_unique_id = f"{account_uid(entry)}_{description.key}"
        self._attr_device_info = account_device_info(entry)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
