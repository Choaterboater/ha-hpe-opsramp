"""The OpsRamp client against fake OpsRamp answers: token reuse and refresh, paging, query encoding, errors.

Sample data, not from a real network.
"""

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hpe_opsramp.api import (
    OpsRampAuthError,
    OpsRampBusyError,
    OpsRampClient,
    OpsRampError,
    clean_base_url,
    valid_tenant_id,
)

BASE = "https://example.api.opsramp.com"
TOKEN = f"{BASE}/tenancy/auth/oauth/token"
TENANT = "client_00001"
T = f"{BASE}/api/v2/tenants/{TENANT}"
SECRET = "test-secret-do-not-log"


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_opsramp.api.MIN_REQUEST_GAP_SECONDS", 0)


def client(hass):
    return OpsRampClient(async_get_clientsession(hass), f"{BASE}/api/", TENANT, "key", SECRET)


def token_calls(aioclient_mock) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN)


async def test_token_reused_and_alert_search_pages(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, json={"access_token": "tok-1", "token_type": "bearer", "expires_in": "7199"})
    # The mocker matches the first registered URL whose query is a subset, so the later page goes first.
    aioclient_mock.get(f"{T}/alerts/search", params={"pageNo": "2"},
                       json={"results": [{"id": "2"}], "totalResults": 2, "nextPage": False})
    aioclient_mock.get(f"{T}/alerts/search", json={"results": [{"id": "1"}], "totalResults": 2, "nextPage": True})
    c = client(hass)
    assert await c.alerts() == [{"id": "1"}, {"id": "2"}]
    assert await c.alerts() == [{"id": "1"}, {"id": "2"}]
    assert token_calls(aioclient_mock) == 1
    token_call = next(x for x in aioclient_mock.mock_calls if str(x[1]) == TOKEN)
    assert token_call[2] == {"grant_type": "client_credentials", "client_id": "key", "client_secret": SECRET}
    searches = [x for x in aioclient_mock.mock_calls if "alerts/search" in str(x[1])]
    assert all(x[3]["Authorization"] == "Bearer tok-1" for x in searches)
    assert "queryString=states:CRITICAL,WARNING&pageNo=1&pageSize=500" in str(searches[0][1])


async def test_space_in_a_query_is_sent_as_percent_20(hass, aioclient_mock):
    """OpsRamp reads + as its field separator, so "On Hold" must not become "On+Hold"."""
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    aioclient_mock.get(f"{T}/incidents/search", json={"results": [{"id": "INC1"}], "totalResults": 1})
    assert await client(hass).incidents() == [{"id": "INC1"}]
    url = str(next(x[1] for x in aioclient_mock.mock_calls if "incidents" in str(x[1])))
    assert "status:New,Open,Pending,On%20Hold" in url and "+" not in url


async def test_find_resources_drops_query_syntax_and_falls_back_to_host_name(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    aioclient_mock.get(f"{T}/resources/search", params={"queryString": "resourceName:web state inactive"},
                       json={"results": [], "totalResults": 0})
    aioclient_mock.get(f"{T}/resources/search", params={"queryString": "hostName:web state inactive"},
                       json={"results": [{"id": "r1", "hostName": "web-1"}], "totalResults": 1})
    c = client(hass)
    assert await c.find_resources("web+state:inactive", 5) == [{"id": "r1", "hostName": "web-1"}]
    assert await c.find_resources(" +:, ", 5) == []
    urls = [str(x[1]) for x in aioclient_mock.mock_calls if "resources" in str(x[1])]
    assert len(urls) == 2 and all("%2B" not in u and "inactive&" in u for u in urls)


async def test_resources_total(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    aioclient_mock.get(f"{T}/resources/search", params={"queryString": "state:active", "pageSize": "1"},
                       json={"results": [{"id": "r1"}], "totalResults": 42, "nextPage": True})
    assert await client(hass).resources_total() == 42


async def test_401_refreshes_token_once_then_fails(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    aioclient_mock.get(f"{T}/alerts/search", status=401)
    with pytest.raises(OpsRampAuthError) as info:
        await client(hass).alerts()
    assert token_calls(aioclient_mock) == 2
    assert SECRET not in str(info.value)


async def test_rate_limited_with_reset_time(hass, aioclient_mock, freezer):
    freezer.move_to("2026-10-23 12:00:00+00:00")
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    reset = 1792756800 + 30  # 2026-10-23 12:00:30 UTC, the way OpsRamp gives x-ratelimit-reset
    aioclient_mock.get(f"{T}/alerts/search", status=429, headers={"X-RateLimit-Reset": str(reset)})
    with pytest.raises(OpsRampBusyError) as info:
        await client(hass).alerts()
    assert 25 <= info.value.retry_after <= 30


async def test_bad_credentials_never_echo_the_body(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, status=401, text=f'{{"error":"invalid_client","client_secret":"{SECRET}"}}')
    with pytest.raises(OpsRampAuthError) as info:
        await client(hass).check()
    assert SECRET not in str(info.value)


async def test_alert_action_posts_and_checks_the_id(hass, aioclient_mock):
    aioclient_mock.post(TOKEN, json={"access_token": "tok", "expires_in": 7199})
    aioclient_mock.post(f"{T}/alerts/123/actions/acknowledge", text="")
    c = client(hass)
    await c.alert_action("123", "acknowledge", "on it")
    call = next(x for x in aioclient_mock.mock_calls if "actions" in str(x[1]))
    assert call[0] == "POST" and call[2] == {"description": "on it"}
    with pytest.raises(OpsRampError):
        await c.alert_action("../123", "acknowledge")
    with pytest.raises(OpsRampError):
        await c.alert_action("123", "delete")
    assert sum(1 for x in aioclient_mock.mock_calls if "actions" in str(x[1])) == 1


def test_clean_base_url_and_tenant_id():
    assert clean_base_url("example.api.opsramp.com/api/") == BASE
    assert clean_base_url("https://example.api.opsramp.com/api/v2") == BASE
    assert valid_tenant_id(" client_00001 ") and valid_tenant_id("00000000-0000-0000-0000-000000000000")
    assert not valid_tenant_id("client/../x") and not valid_tenant_id("")
