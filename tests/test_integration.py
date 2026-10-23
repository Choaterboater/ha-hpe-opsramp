"""Config flow, options, sweeps, entities, events, the alert action and the Assist tools, with a fake OpsRamp.

Sample data, not from a real network.
"""

import asyncio
from datetime import timedelta

import probatio
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hpe_opsramp.const import DOMAIN, POLL_SECONDS

BASE = "https://example.api.opsramp.com"
TOKEN = f"{BASE}/tenancy/auth/oauth/token"
TENANT = "client_00001"
T = f"{BASE}/api/v2/tenants/{TENANT}"
SECRET = "test-secret-do-not-log"

CPU = {"id": 1001, "currentState": "CRITICAL", "subject": "CPU above 95% on web-1", "description": "CPU utilization is 97%",
       "resource": {"id": "r1", "name": "web-1"}, "serviceName": "System Performance", "metric": "system.cpu.utilization",
       "component": "CPU", "app": "OPSRAMP", "alertType": "Monitoring", "priority": "P1", "status": "Open",
       "acknowledged": False, "suppressed": False, "ticketed": False, "closed": False,
       "createdDate": "2026-10-23T09:00:00+0000", "elapsedTimeString": "2 hours", "repeatCount": "3"}
DISK = {"id": 1002, "currentState": "WARNING", "subject": "Disk 85% full on db-1", "device": {"id": "r2", "name": "db-1"},
        "priority": "P3", "status": "Acknowledged", "acknowledged": True, "createdDate": "2026-10-23T10:00:00+0000"}
PING = {"id": 1004, "currentState": "CRITICAL", "subject": "Ping lost to printer-1", "resource": {"name": "printer-1"},
        "createdDate": "2026-10-23T11:00:00+0000"}
HEALED = {"id": 1003, "currentState": "OK", "subject": "Ping restored on web-2", "createdDate": "2026-10-23T08:00:00+0000"}
INCIDENT = {"id": "INC0000001", "subject": "web-1 is slow", "status": "Open", "priority": "High",
            "createdDate": "2026-10-23T09:05:00+0000"}
RESOURCE = {"id": "r1", "name": "web-1", "hostName": "web-1.example.com", "ipAddress": "192.0.2.10",
            "resourceType": "DEVICE", "state": "active"}


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_opsramp.api.MIN_REQUEST_GAP_SECONDS", 0)


def opsramp(aioclient_mock, alerts=None, incidents=None, token_status=200, alert_status=200, incident_status=200):
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN, status=token_status, json={"access_token": "tok", "token_type": "bearer", "expires_in": 7199})
    rows = alerts if alerts is not None else [CPU, DISK, HEALED]
    aioclient_mock.get(f"{T}/alerts/search", status=alert_status,
                       json={"results": rows, "totalResults": len(rows), "nextPage": False})
    inc = incidents if incidents is not None else [INCIDENT]
    aioclient_mock.get(f"{T}/incidents/search", status=incident_status, json={"results": inc, "totalResults": len(inc)})
    aioclient_mock.get(f"{T}/resources/search", params={"queryString": "state:active"},
                       json={"results": [RESOURCE], "totalResults": 12, "nextPage": True})
    aioclient_mock.get(f"{T}/resources/search", json={"results": [RESOURCE], "totalResults": 1})
    aioclient_mock.get(f"{T}/alerts/1001", json=CPU)
    aioclient_mock.get(f"{T}/alerts/999", status=404)
    for action in ("acknowledge", "close"):
        aioclient_mock.post(f"{T}/alerts/1001/actions/{action}", text="")


def posts(aioclient_mock) -> list:
    return [c for c in aioclient_mock.mock_calls if "/actions/" in str(c[1])]


async def settle(hass):
    for _ in range(3):
        await hass.async_block_till_done()
        pending = [t for t in hass._background_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
    await hass.async_block_till_done()


async def tick(hass, freezer, seconds=POLL_SECONDS + 1):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def entry(allow_actions=False):
    return MockConfigEntry(
        domain=DOMAIN,
        title="HPE OpsRamp",
        unique_id=TENANT,
        data={"base_url": BASE, "tenant_id": TENANT, "client_id": "key", "client_secret": SECRET},
        options={"scan_seconds": POLL_SECONDS, "allow_actions": allow_actions},
    )


async def setup(hass, e=None):
    e = e or entry()
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    return e


def llm_context(user_id=None):
    return llm.LLMContext(platform="test", context=Context(user_id=user_id), language="en",
                          assistant="conversation", device_id=None)


async def assist(hass, user_id=None):
    api = await llm.async_get_api(hass, DOMAIN, llm_context(user_id))
    return api, {t.name: t for t in api.tools}


async def run(api, tool, args):
    return await tool.async_call(api.api.hass, llm.ToolInput(tool.name, args), api.llm_context)


# --- flows ------------------------------------------------------------------------


async def test_config_flow_creates_one_entry_per_tenant(hass, aioclient_mock):
    opsramp(aioclient_mock)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "base_url": "example.api.opsramp.com/api/", "tenant_id": f" {TENANT} ", "client_id": " key ", "client_secret": SECRET})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "HPE OpsRamp"
    assert r["data"] == {"base_url": BASE, "tenant_id": TENANT, "client_id": "key", "client_secret": SECRET}
    assert r["result"].unique_id == TENANT
    assert r["result"].options == {"scan_seconds": POLL_SECONDS, "allow_actions": False}  # read-only by default
    await settle(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "base_url": BASE, "tenant_id": TENANT, "client_id": "key", "client_secret": SECRET})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_config_flow_errors(hass, aioclient_mock):
    opsramp(aioclient_mock, token_status=401)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    form = {"base_url": BASE, "tenant_id": TENANT, "client_id": "x", "client_secret": "y"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], form)
    assert r["errors"] == {"base": "invalid_auth"}
    opsramp(aioclient_mock, alert_status=500)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], form)
    assert r["errors"] == {"base": "cannot_connect"}
    aioclient_mock.clear_requests()
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**form, "tenant_id": "client/../x"})
    assert r["errors"] == {"tenant_id": "invalid_tenant"}
    assert aioclient_mock.mock_calls == []  # a bad tenant id never reaches OpsRamp


# --- entities and events --------------------------------------------------------------


async def test_entities(hass, aioclient_mock):
    opsramp(aioclient_mock)
    await setup(hass)
    s = hass.states.get
    open_alerts = s("sensor.hpe_opsramp_open_alerts")
    assert open_alerts.state == "2"  # the OK one is not open
    assert [a["id"] for a in open_alerts.attributes["alerts"]] == ["1001", "1002"]  # critical first
    assert open_alerts.attributes["alerts"][0] == {
        "id": "1001", "state": "CRITICAL", "subject": "CPU above 95% on web-1", "resource": "web-1", "priority": "P1",
        "acknowledged": False, "created": "2026-10-23T09:00:00+0000"}
    assert open_alerts.attributes["alerts"][1]["resource"] == "db-1"  # from "device"
    assert s("sensor.hpe_opsramp_critical_alerts").state == "1"
    assert s("sensor.hpe_opsramp_warning_alerts").state == "1"
    assert s("sensor.hpe_opsramp_unacknowledged_alerts").state == "1"
    incidents = s("sensor.hpe_opsramp_open_incidents")
    assert incidents.state == "1" and incidents.attributes["incidents"][0]["id"] == "INC0000001"
    assert s("sensor.hpe_opsramp_active_resources").state == "12"
    assert s("binary_sensor.hpe_opsramp_critical_alert").state == STATE_ON


async def test_events_for_opened_and_cleared_alerts(hass, aioclient_mock, freezer):
    opsramp(aioclient_mock)
    await setup(hass)
    fired = []

    @callback
    def record(event):
        if event.data["entity_id"] == "event.hpe_opsramp_events" and (new := event.data["new_state"]) is not None:
            fired.append(dict(new.attributes))

    hass.bus.async_listen("state_changed", record)
    opsramp(aioclient_mock, alerts=[DISK, PING])
    await tick(hass, freezer)
    assert [(f["event_type"], f["id"]) for f in fired] == [("alert_opened", "1004"), ("alert_cleared", "1001")]
    assert fired[0]["subject"] == "Ping lost to printer-1" and fired[0]["state"] == "CRITICAL"
    opsramp(aioclient_mock, alerts=[DISK])
    await tick(hass, freezer)
    assert hass.states.get("binary_sensor.hpe_opsramp_critical_alert").state == STATE_OFF


async def test_incidents_not_readable_leaves_them_unknown(hass, aioclient_mock):
    opsramp(aioclient_mock, incident_status=403)
    await setup(hass)
    assert hass.states.get("sensor.hpe_opsramp_open_incidents").state == STATE_UNKNOWN
    assert hass.states.get("sensor.hpe_opsramp_open_alerts").state == "2"


async def test_outage_keeps_last_state_then_unavailable_and_secret_never_logged(hass, aioclient_mock, freezer, caplog):
    opsramp(aioclient_mock)
    await setup(hass)
    opsramp(aioclient_mock, alert_status=503)
    await tick(hass, freezer)
    assert hass.states.get("sensor.hpe_opsramp_open_alerts").state == "2"  # kept through short trouble
    assert "keeping last state" in caplog.text
    await tick(hass, freezer, 400)
    assert hass.states.get("sensor.hpe_opsramp_open_alerts").state == STATE_UNAVAILABLE
    opsramp(aioclient_mock)
    await tick(hass, freezer, 301)
    assert hass.states.get("sensor.hpe_opsramp_open_alerts").state == "2"
    assert SECRET not in caplog.text


async def test_refused_credentials_retry_setup(hass, aioclient_mock):
    opsramp(aioclient_mock, token_status=401)
    e = entry()
    e.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(e.entry_id)
    assert e.state is ConfigEntryState.SETUP_RETRY


# --- the alert action ------------------------------------------------------------------


async def test_alert_action_is_off_until_allowed_in_the_options(hass, aioclient_mock, hass_read_only_user):
    opsramp(aioclient_mock)
    e = await setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "alert_action", {"alert_id": "1001", "action": "acknowledge"}, blocking=True)
    assert posts(aioclient_mock) == []

    r = await hass.config_entries.options.async_init(e.entry_id)
    assert r["data_schema"]({})["allow_actions"] is False
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_seconds": 90, "allow_actions": True})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert e.options == {"scan_seconds": 90, "allow_actions": True}
    assert e.runtime_data.allow_actions and e.runtime_data.update_interval == timedelta(seconds=90)

    with pytest.raises(Unauthorized):  # admins only
        await hass.services.async_call(DOMAIN, "alert_action", {"alert_id": "1001", "action": "acknowledge"},
                                       blocking=True, context=Context(user_id=hass_read_only_user.id))
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "alert_action", {"alert_id": "../1001", "action": "close"}, blocking=True)
    assert posts(aioclient_mock) == []

    searches = sum(1 for c in aioclient_mock.mock_calls if "alerts/search" in str(c[1]))
    await hass.services.async_call(DOMAIN, "alert_action", {"alert_id": "1001", "action": "acknowledge", "comment": "on it"},
                                   blocking=True)
    await settle(hass)
    [call] = posts(aioclient_mock)
    assert str(call[1]) == f"{T}/alerts/1001/actions/acknowledge" and call[2] == {"description": "on it"}
    assert sum(1 for c in aioclient_mock.mock_calls if "alerts/search" in str(c[1])) > searches  # checked again after


# --- Assist tools ----------------------------------------------------------------------


async def test_assist_tools_read(hass, aioclient_mock):
    opsramp(aioclient_mock)
    await setup(hass)
    api, tools = await assist(hass)
    assert set(tools) == {"opsramp_list_alerts", "opsramp_list_incidents", "opsramp_get_alert", "opsramp_find_resources"}
    for tool in tools.values():  # what a conversation agent sends the model
        schema = probatio.to_openapi(tool.parameters, custom_serializer=llm.selector_serializer, openapi_version="3.1.0")
        assert schema["type"] == "object"
        assert tool.annotations.read_only and tool.integration == DOMAIN

    r = await run(api, tools["opsramp_list_alerts"], {})
    assert not r.error and r.data["total"] == 2 and r.data["alert_actions_allowed"] is False
    assert [a["id"] for a in r.data["alerts"]] == ["1001", "1002"]
    r = await run(api, tools["opsramp_list_alerts"], {"unacknowledged_only": True})
    assert [a["id"] for a in r.data["alerts"]] == ["1001"]
    r = await run(api, tools["opsramp_list_alerts"], {"text": "DB-1"})
    assert [a["id"] for a in r.data["alerts"]] == ["1002"]
    r = await run(api, tools["opsramp_list_alerts"], {"state": "OK"})
    assert r.error and "Bad arguments" in r.data["error"]

    r = await run(api, tools["opsramp_list_incidents"], {"limit": 5})
    assert r.data["total"] == 1 and r.data["incidents"][0]["subject"] == "web-1 is slow"
    r = await run(api, tools["opsramp_get_alert"], {"alert_id": "1001"})
    assert r.data["alert"]["description"] == "CPU utilization is 97%" and r.data["alert"]["repeat_count"] == 3
    r = await run(api, tools["opsramp_get_alert"], {"alert_id": "999"})
    assert r.error and "999" in r.data["error"]
    r = await run(api, tools["opsramp_find_resources"], {"name": "web"})
    assert r.data["resources"] == [{"id": "r1", "name": "web-1", "host": "web-1.example.com", "ip": "192.0.2.10",
                                    "type": "DEVICE", "state": "active"}]
    assert posts(aioclient_mock) == []


async def test_assist_action_tool_only_when_allowed_and_admins_only(hass, aioclient_mock, hass_read_only_user):
    opsramp(aioclient_mock)
    await setup(hass, entry(allow_actions=True))
    api, tools = await assist(hass)
    assert "opsramp_alert_action" in tools and "opsramp_alert_action" in api.api_prompt
    assert not tools["opsramp_alert_action"].annotations.read_only
    r = await run(api, tools["opsramp_alert_action"], {"alert_id": "1001", "action": "close", "comment": "fixed"})
    assert r.data == {"done": True, "alert_id": "1001", "action": "close"}
    [call] = posts(aioclient_mock)
    assert str(call[1]).endswith("/alerts/1001/actions/close") and call[2] == {"description": "fixed"}

    api, tools = await assist(hass, hass_read_only_user.id)
    r = await run(api, tools["opsramp_alert_action"], {"alert_id": "1001", "action": "acknowledge"})
    assert r.error
    assert len(posts(aioclient_mock)) == 1


async def test_assist_tools_without_a_tenant(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    api, tools = await assist(hass)
    r = await run(api, tools["opsramp_list_alerts"], {})
    assert r.error and "No HPE OpsRamp tenant" in r.data["error"]
