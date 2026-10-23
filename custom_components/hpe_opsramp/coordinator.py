"""Keeps an OpsRamp tenant's open alerts, open incidents and active resource count from REST sweeps."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ALERT_ACTIONS,
    CONF_ALLOW_ACTIONS,
    CONF_SCAN,
    DEFAULT_ALLOW_ACTIONS,
    DOMAIN,
    EVENT_ALERT_CLEARED,
    EVENT_ALERT_OPENED,
    MAX_BACKOFF_SECONDS,
    OPEN_ALERT_STATES,
    POLL_SECONDS,
    UNAVAILABLE_AFTER_SECONDS,
)
from .errors import SourceBusyError, SourceError
from .source import Alert, OpsRampSource, Snapshot

_LOGGER = logging.getLogger(__name__)


def event_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


def unacknowledged(alert: Alert) -> bool:
    return not alert.get("acknowledged") and not alert.get("suppressed")


def sorted_alerts(alerts: Iterable[Alert]) -> list[Alert]:
    """Critical first, then warning; newest first within each."""
    newest = sorted(alerts, key=lambda a: a.get("created") or "", reverse=True)
    rank = {s: n for n, s in enumerate(OPEN_ALERT_STATES)}
    return sorted(newest, key=lambda a: rank.get(a.get("state") or "", len(rank)))


class HpeOpsRampCoordinator(DataUpdateCoordinator[Snapshot]):
    """One coordinator per OpsRamp tenant (config entry): a sweep every scan_seconds."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: OpsRampSource) -> None:
        self.poll_seconds = int(entry.options.get(CONF_SCAN, POLL_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=self.poll_seconds),
        )
        self.source = source
        self.allow_actions: bool = bool(entry.options.get(CONF_ALLOW_ACTIONS, DEFAULT_ALLOW_ACTIONS))
        self.last_ok: float | None = None
        self._backoff = 0.0

    async def _async_update_data(self) -> Snapshot:
        now = time.time()
        try:
            snap = await self.source.sweep()
        except SourceError as err:
            return self._failed(err, now)
        self._backoff = 0.0
        self._set_interval()
        self.last_ok = now
        if self.data is not None:
            self._events(self.data, snap)
        return snap

    def _set_interval(self) -> None:
        self.update_interval = timedelta(seconds=max(self.poll_seconds, self._backoff))

    def _failed(self, err: SourceError, now: float) -> Snapshot:
        """Keep the last state through short trouble (at least two sweeps); unavailable after that."""
        if isinstance(err, SourceBusyError):
            self._backoff = min(MAX_BACKOFF_SECONDS, max(self._backoff * 2 or self.poll_seconds * 2, err.retry_after or 0))
            self._set_interval()
        keep = max(UNAVAILABLE_AFTER_SECONDS, 2 * self.poll_seconds + 15)
        if self.data is not None and self.last_ok is not None and now - self.last_ok < keep:
            _LOGGER.warning("OpsRamp check failed (%s); keeping last state, next try in %ss",
                            err, int(self.update_interval.total_seconds()) if self.update_interval else self.poll_seconds)
            return self.data
        raise UpdateFailed(str(err)) from err

    async def async_alert_action(self, alert_id: str, action: str, comment: str = "") -> None:
        """WRITE: one alert action in OpsRamp (callers check allow_actions first), then a fresh sweep."""
        await self.source.client.alert_action(alert_id, ALERT_ACTIONS[action], comment)
        await self.async_request_refresh()

    # --- events -----------------------------------------------------------

    def _events(self, old: Snapshot, new: Snapshot) -> None:
        """Alerts that opened or cleared since the last good sweep."""
        for aid, alert in new.alerts.items():
            if aid not in old.alerts:
                self._emit(EVENT_ALERT_OPENED, _alert_event(alert))
        for aid, alert in old.alerts.items():
            if aid not in new.alerts:
                self._emit(EVENT_ALERT_CLEARED, _alert_event(alert))

    @callback
    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.config_entry:
            async_dispatcher_send(self.hass, event_signal(self.config_entry.entry_id), event_type, data)


def _alert_event(alert: Alert) -> dict[str, Any]:
    return {k: alert.get(k) for k in ("id", "state", "subject", "resource", "priority", "created")}
