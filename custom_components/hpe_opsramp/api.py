"""Small async client for the HPE OpsRamp REST API (no Home Assistant imports, so it is testable alone).

Authentication is the OAuth client-credentials grant of an OpsRamp custom
integration (key and secret) on the tenant's own API host; the token is cached,
refreshed a minute before it expires and once more on a 401 / 407. Searches page
with pageNo and pageSize. The only write call is an alert action, which the
integration makes only when the entry's options allow it.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from yarl import URL

from .const import (
    ALERT_ACTIONS,
    ALERT_PAGE_SIZE,
    ALERT_QUERY,
    INCIDENT_PAGE_SIZE,
    INCIDENT_QUERY,
    MAX_PAGES,
    MIN_REQUEST_GAP_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    RESOURCE_QUERY,
    TENANTS_PATH,
    TOKEN_PATH,
)
from .errors import SourceAuthError, SourceBusyError, SourceError

_ALERT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_TENANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# "+" joins query fields, ":" separates a field from its value and "," lists values.
_QUERY_SYNTAX = re.compile(r"[+:,]")


class OpsRampError(SourceError):
    """OpsRamp could not be reached or answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OpsRampAuthError(OpsRampError, SourceAuthError):
    """The key / secret or the token was refused, or the integration may not read this."""


class OpsRampBusyError(OpsRampError, SourceBusyError):
    """OpsRamp asked us to slow down (429) or had a server error (5xx)."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"OpsRamp answered {status}", status)
        self.retry_after = retry_after


def clean_base_url(url: str) -> str:
    """"acme.api.opsramp.com/api/" -> "https://acme.api.opsramp.com"."""
    url = url.strip().rstrip("/")
    url = url if url.startswith(("https://", "http://")) else f"https://{url}"
    for suffix in ("/api/v2", "/api"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def valid_alert_id(alert_id: str) -> bool:
    return bool(_ALERT_ID.match(alert_id))


def valid_tenant_id(tenant_id: str) -> bool:
    return bool(_TENANT_ID.match(tenant_id.strip()))


def _url(url: str, params: dict[str, str] | None) -> URL:
    """OpsRamp reads "+" in a queryString as its field separator, so a space must go as %20, not +."""
    if not params:
        return URL(url)
    return URL(f"{url}?{urlencode(params, quote_via=quote, safe=':,')}", encoded=True)


class OpsRampClient:
    """Read calls (and the alert action) for one OpsRamp tenant."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._session = session
        self._base = clean_base_url(base_url)
        self._tenant = f"{TENANTS_PATH}/{tenant_id.strip()}"
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._token: str | None = None
        self._token_exp = 0.0
        self._token_lock = asyncio.Lock()
        self._pace_lock = asyncio.Lock()
        self._last_request = 0.0

    # --- token ---------------------------------------------------------------

    async def token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            try:
                async with self._session.post(
                    self._base + TOKEN_PATH,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    # Never echo the body: it may contain secrets.
                    if resp.status in (400, 401, 403):
                        raise OpsRampAuthError(f"OpsRamp refused the key / secret ({resp.status})", resp.status)
                    if resp.status == 429 or resp.status >= 500:
                        raise OpsRampBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status != 200:
                        raise OpsRampError(f"OpsRamp token endpoint answered {resp.status}", resp.status)
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise OpsRampError(f"Cannot reach OpsRamp: {type(err).__name__}") from err
            if not isinstance(body, dict) or not body.get("access_token"):
                raise OpsRampError("OpsRamp token endpoint gave no token")
            self._token = body["access_token"]
            self._token_exp = time.time() + (_int(body.get("expires_in")) or 7199)
            return self._token

    # --- requests ------------------------------------------------------------

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._last_request + MIN_REQUEST_GAP_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def request(self, method: str, path: str, params: dict[str, str] | None = None, body: Any = None) -> Any:
        """One call under this tenant; retried once with a fresh token on 401 / 407. Secrets never appear in errors."""
        for attempt in (0, 1):
            token = await self.token(force=attempt == 1)
            await self._pace()
            try:
                async with self._session.request(
                    method,
                    _url(self._base + self._tenant + path, params),
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status in (401, 407) and attempt == 0:
                        continue
                    if resp.status in (401, 403, 407, 410):
                        raise OpsRampAuthError(f"OpsRamp refused the request ({resp.status} for {method} {path})", resp.status)
                    if resp.status == 429 or resp.status >= 500:
                        raise OpsRampBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status >= 400:
                        raise OpsRampError(f"OpsRamp answered {resp.status} for {method} {path}", resp.status)
                    text = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise OpsRampError(f"Cannot reach OpsRamp: {type(err).__name__}") from err
            return _json(text)
        raise OpsRampAuthError("OpsRamp refused the token", 401)

    async def search(
        self, path: str, query: str, page_size: int, max_pages: int = MAX_PAGES
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Rows of a search endpoint, page by page, and the total OpsRamp reports."""
        rows: list[dict[str, Any]] = []
        total: int | None = None
        for page_no in range(1, max_pages + 1):
            page = await self.request("GET", path, {"queryString": query, "pageNo": str(page_no), "pageSize": str(page_size)})
            if not isinstance(page, dict):
                break
            got = [r for r in page.get("results") or [] if isinstance(r, dict)]
            rows.extend(got)
            total = _int(page.get("totalResults"))
            if not got or not page.get("nextPage") or (total is not None and len(rows) >= total):
                break
        return rows, total

    # --- endpoints -------------------------------------------------------------

    async def check(self) -> None:
        """One small alert search: proves the key, secret, tenant and host."""
        await self.search("/alerts/search", ALERT_QUERY, 1, 1)

    async def alerts(self) -> list[dict[str, Any]]:
        return (await self.search("/alerts/search", ALERT_QUERY, ALERT_PAGE_SIZE))[0]

    async def alert(self, alert_id: str) -> dict[str, Any]:
        _check_alert_id(alert_id)
        return await self.request("GET", f"/alerts/{alert_id}") or {}

    async def incidents(self) -> list[dict[str, Any]]:
        return (await self.search("/incidents/search", INCIDENT_QUERY, INCIDENT_PAGE_SIZE, 2))[0]

    async def resources_total(self) -> int:
        rows, total = await self.search("/resources/search", RESOURCE_QUERY, 1, 1)
        return total if total is not None else len(rows)

    async def find_resources(self, name: str, limit: int) -> list[dict[str, Any]]:
        """Resources by name, else by host name. Query syntax in the name is dropped, so it cannot add filters."""
        text = " ".join(_QUERY_SYNTAX.sub(" ", name).split())
        if not text:
            return []
        for field in ("resourceName", "hostName"):
            rows, _ = await self.search("/resources/search", f"{field}:{text}", limit, 1)
            if rows:
                return rows
        return []

    async def alert_action(self, alert_id: str, action: str, comment: str = "") -> None:
        """WRITE: acknowledge, suppress, close... one alert (OpsRamp's action name, see ALERT_ACTIONS)."""
        _check_alert_id(alert_id)
        if action not in ALERT_ACTIONS.values():
            raise OpsRampError(f"{action!r} is not an OpsRamp alert action")
        await self.request("POST", f"/alerts/{alert_id}/actions/{action}", body={"description": comment} if comment else {})


def _check_alert_id(alert_id: str) -> None:
    if not valid_alert_id(alert_id):
        raise OpsRampError(f"{alert_id!r} is not an OpsRamp alert id", 400)


def _json(text: str) -> Any:
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after(headers: Any) -> float | None:
    for key in ("Retry-After", "X-RateLimit-Reset"):
        try:
            if value := headers.get(key):
                seconds = float(value)
                if seconds > 1e9:  # OpsRamp gives the reset as a Unix time
                    seconds -= time.time()
                return max(0.0, seconds)
        except (TypeError, ValueError):
            continue
    return None
