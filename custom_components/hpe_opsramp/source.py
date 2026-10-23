"""Where OpsRamp alerts, incidents and resources come from.

Everything is normalised here into the plain dicts below, so the rest of the
code never sees the OpsRamp API.

Alert:    id, state ("CRITICAL" | "WARNING" | ...), subject, description, resource,
          resource_id, service, metric, component, app, alert_type, priority ("P0".."P5"),
          status, acknowledged, suppressed, ticketed, closed (bools), created, updated,
          elapsed, repeat_count.
Incident: id, subject, status, priority, created, updated.
Resource: id, name, host, ip, type, state.
Any value may be None when OpsRamp does not report it. Open alerts and open
incidents are read every sweep (incidents are None when the key may not read
them); the active resource count at most every RESOURCES_SECONDS.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .api import OpsRampBusyError, OpsRampClient, OpsRampError
from .const import OPEN_ALERT_STATES, RESOURCES_SECONDS

_LOGGER = logging.getLogger(__name__)

Alert = dict[str, Any]
Incident = dict[str, Any]
Resource = dict[str, Any]


@dataclass
class Snapshot:
    """One full sweep."""

    alerts: dict[str, Alert] = field(default_factory=dict)  # id -> open alert
    incidents: list[Incident] | None = None
    resources: int | None = None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    return str(value) if isinstance(value, int) else _text(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip(value: Any, chars: int) -> str | None:
    text = _text(value)
    return text if text is None or len(text) <= chars else text[: chars - 1] + "…"


def _resource(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """The alerted resource's name and id (OpsRamp puts it in "resource" or "device")."""
    for key in ("resource", "device"):
        r = row.get(key)
        if isinstance(r, dict) and (name := _text(r.get("name")) or _text(r.get("hostName")) or _text(r.get("resourceName"))):
            return name, _id(r.get("id"))
    return None, None


def alert_from(row: dict[str, Any], description_chars: int = 500) -> Alert:
    resource, resource_id = _resource(row)
    state = _text(row.get("currentState"))
    return {
        "id": _id(row.get("id")) or _id(row.get("uniqueId")) or "",
        "state": state.upper() if state else None,
        "subject": _text(row.get("subject")),
        "description": _clip(row.get("description"), description_chars),
        "resource": resource,
        "resource_id": resource_id,
        "service": _text(row.get("serviceName")),
        "metric": _text(row.get("metric")),
        "component": _text(row.get("component")),
        "app": _text(row.get("app")),
        "alert_type": _text(row.get("alertType")),
        "priority": _text(row.get("priority")),
        "status": _text(row.get("status")),
        "acknowledged": row.get("acknowledged") is True,
        "suppressed": row.get("suppressed") is True,
        "ticketed": row.get("ticketed") is True,
        "closed": row.get("closed") is True,
        "created": _text(row.get("createdDate")),
        "updated": _text(row.get("updatedTime")),
        "elapsed": _text(row.get("elapsedTimeString")),
        "repeat_count": _int(row.get("repeatCount")),
    }


def is_open(alert: Alert) -> bool:
    return alert["state"] in OPEN_ALERT_STATES and not alert["closed"] and (alert["status"] or "").upper() != "CLOSED"


def incident_from(row: dict[str, Any]) -> Incident:
    return {
        "id": _id(row.get("id")) or "",
        "subject": _text(row.get("subject")),
        "status": _text(row.get("status")),
        "priority": _text(row.get("priority")),
        "created": _text(row.get("createdDate")),
        "updated": _text(row.get("updatedDate")),
    }


def resource_from(row: dict[str, Any]) -> Resource:
    return {
        "id": _id(row.get("id")),
        "name": next((n for k in ("name", "resourceName", "hostName", "aliasName") if (n := _text(row.get(k)))), None),
        "host": _text(row.get("hostName")),
        "ip": _text(row.get("ipAddress")),
        "type": _text(row.get("resourceType")) or _text(row.get("type")),
        "state": _text(row.get("state")),
    }


class OpsRampSource:
    """Sweeps one OpsRamp tenant."""

    def __init__(self, client: OpsRampClient) -> None:
        self.client = client
        self._incidents: list[Incident] | None = None
        self._resources: int | None = None
        self._resources_at = 0.0

    async def sweep(self) -> Snapshot:
        """Everything now. Raises SourceError (or a subclass) when the alerts cannot be read."""
        alerts: dict[str, Alert] = {}
        for row in await self.client.alerts():
            alert = alert_from(row)
            if alert["id"] and is_open(alert):
                alerts[alert["id"]] = alert
        try:
            self._incidents = [incident_from(r) for r in await self.client.incidents()]
        except OpsRampError as err:
            self._incidents = self._skipped(err, self._incidents, "incidents")
        now = time.time()
        if now - self._resources_at >= RESOURCES_SECONDS:
            try:
                self._resources = await self.client.resources_total()
            except OpsRampError as err:
                self._resources = self._skipped(err, self._resources, "resources")
            self._resources_at = now
        return Snapshot(alerts=alerts, incidents=self._incidents, resources=self._resources)

    @staticmethod
    def _skipped(err: OpsRampError, last: Any, what: str) -> Any:
        """An optional read failed: 401 fails the sweep; no answer, 429 and 5xx keep the last value;
        other refusals (403, 404, 400) leave it out."""
        if err.status == 401:
            raise err
        if err.status is None or isinstance(err, OpsRampBusyError):
            return last
        _LOGGER.debug("OpsRamp %s skipped: %s", what, err)
        return None
