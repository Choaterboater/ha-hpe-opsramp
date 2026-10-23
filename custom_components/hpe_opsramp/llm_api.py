"""Assist (LLM) tools for HPE OpsRamp: open alerts and incidents, one alert's details, resources by name, and,
only while an entry's options allow alert actions, a tool that acknowledges, suppresses or closes an alert.

Pick "HPE OpsRamp" under the conversation agent's "Control Home Assistant" options to use them.
The action tool runs the hpe_opsramp.alert_action action as the person talking, so it is admins only too.
"""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .api import OpsRampError, valid_alert_id
from .const import ALERT_ACTIONS, DOMAIN, MAX_TOOL_ROWS, OPEN_ALERT_STATES
from .coordinator import HpeOpsRampCoordinator, sorted_alerts, unacknowledged
from .services import SERVICE_ALERT_ACTION
from .source import Alert, alert_from, resource_from

LLM_API_ID = DOMAIN
LLM_API_NAME = "HPE OpsRamp"
API_PROMPT = (
    "Tools for HPE OpsRamp, an IT operations platform: open alerts (CRITICAL or WARNING) and open incidents "
    "from its last check, one alert's details, and monitored resources by name. Alert ids come from the alert list."
)
ACTION_PROMPT = (
    " opsramp_alert_action changes an alert in OpsRamp: use it only when the user asks for that action "
    "on that alert, and say which alert you acted on."
)

# Lists come from the integration's last check; detail and search calls ask OpsRamp.
_CACHED = llm.ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=False)
_REMOTE = llm.ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=True)
_WRITE = llm.ToolAnnotations(read_only=False, destructive=True, idempotent=False, open_world=True)


def _coordinators(hass: HomeAssistant) -> list[HpeOpsRampCoordinator]:
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def _tenant(c: HpeOpsRampCoordinator, all_: list[HpeOpsRampCoordinator]) -> dict[str, Any]:
    """Name the tenant only when there are several."""
    return {"tenant": c.config_entry.title} if len(all_) > 1 and c.config_entry else {}


def _alert_brief(a: Alert) -> dict[str, Any]:
    return {k: a.get(k) for k in ("id", "state", "subject", "resource", "priority", "acknowledged", "suppressed",
                                  "ticketed", "created", "elapsed")}


class OpsRampTool(llm.Tool):
    """Validates the arguments and turns OpsRamp and Home Assistant errors into an error result."""

    integration = DOMAIN
    annotations = _CACHED

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> llm.ToolResult:
        try:
            args = self.parameters(tool_input.tool_args)
            if not (coordinators := _coordinators(hass)):
                raise HomeAssistantError("No HPE OpsRamp tenant is set up in Home Assistant.")
            return llm.ToolResult(data=await self._run(hass, coordinators, args, llm_context))
        except probatio.Invalid as err:
            return llm.ToolResult(data={"error": f"Bad arguments: {err}"}, error=True)
        except OpsRampError as err:
            return llm.ToolResult(data={"error": f"OpsRamp: {err}"}, error=True)
        except HomeAssistantError as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)

    async def _run(self, hass: HomeAssistant, coordinators: list[HpeOpsRampCoordinator], args: dict[str, Any],
                   llm_context: llm.LLMContext) -> JsonObjectType:
        raise NotImplementedError


class ListAlerts(OpsRampTool):
    name = "opsramp_list_alerts"
    title = "List OpsRamp alerts"
    description = ("Open OpsRamp alerts from the last check, CRITICAL first and newest first. Filter by state, "
                   "by not yet acknowledged, or by text in the subject or resource name.")
    parameters = probatio.Schema({
        probatio.Optional("state", description="Only alerts in this state."): probatio.In(list(OPEN_ALERT_STATES)),
        probatio.Optional("unacknowledged_only", description="Only alerts nobody acknowledged or suppressed.",
                          default=False): bool,
        probatio.Optional("text", description="Only alerts whose subject or resource contains this text."): str,
        probatio.Optional("limit", description="Most alerts to return.", default=20):
            probatio.All(int, probatio.Range(min=1, max=MAX_TOOL_ROWS)),
    })

    async def _run(self, hass, coordinators, args, llm_context):
        text = (args.get("text") or "").casefold().strip()
        rows = []
        for c in coordinators:
            for a in c.data.alerts.values():
                if args.get("state") and a["state"] != args["state"]:
                    continue
                if args["unacknowledged_only"] and not unacknowledged(a):
                    continue
                if text and text not in f"{a.get('subject') or ''} {a.get('resource') or ''}".casefold():
                    continue
                rows.append((a, c))
        ordered = sorted_alerts(a for a, _ in rows)
        owner = {id(a): c for a, c in rows}
        return {
            "total": len(ordered),
            "alerts": [{**_alert_brief(a), **_tenant(owner[id(a)], coordinators)} for a in ordered[: args["limit"]]],
            "alert_actions_allowed": any(c.allow_actions for c in coordinators),
        }


class ListIncidents(OpsRampTool):
    name = "opsramp_list_incidents"
    title = "List OpsRamp incidents"
    description = "Open OpsRamp incidents (New, Open or Pending, created in the last 30 days) from the last check, newest first."
    parameters = probatio.Schema({
        probatio.Optional("limit", description="Most incidents to return.", default=20):
            probatio.All(int, probatio.Range(min=1, max=MAX_TOOL_ROWS)),
    })

    async def _run(self, hass, coordinators, args, llm_context):
        rows, unreadable = [], []
        for c in coordinators:
            if c.data.incidents is None:
                unreadable.append(c.config_entry.title if c.config_entry else "")
                continue
            rows.extend({**i, **_tenant(c, coordinators)} for i in c.data.incidents)
        rows.sort(key=lambda i: i.get("created") or "", reverse=True)
        out: dict[str, Any] = {"total": len(rows), "incidents": rows[: args["limit"]]}
        if unreadable:
            out["not_readable"] = f"This OpsRamp key may not read incidents ({', '.join(unreadable)})."
        return out


class GetAlert(OpsRampTool):
    name = "opsramp_get_alert"
    title = "Get an OpsRamp alert"
    description = ("One OpsRamp alert by its id (from opsramp_list_alerts): state, subject, description, resource, "
                   "service, metric, component, priority, acknowledged, ticketed, times.")
    annotations = _REMOTE
    parameters = probatio.Schema({probatio.Required("alert_id", description="The alert's id."): str})

    async def _run(self, hass, coordinators, args, llm_context):
        alert_id = args["alert_id"].strip()
        if not valid_alert_id(alert_id):
            raise HomeAssistantError(f"{alert_id!r} is not an OpsRamp alert id.")
        for c in sorted(coordinators, key=lambda c: alert_id not in c.data.alerts):
            try:
                row = await c.source.client.alert(alert_id)
            except OpsRampError as err:
                if err.status == 404:
                    continue
                raise
            if row:
                return {"alert": {**alert_from(row, 2000), **_tenant(c, coordinators)}}
        raise HomeAssistantError(f"OpsRamp has no alert {alert_id}.")


class FindResources(OpsRampTool):
    name = "opsramp_find_resources"
    title = "Find OpsRamp resources"
    description = "Resources OpsRamp monitors (servers, network devices, cloud services...) by name or host name."
    annotations = _REMOTE
    parameters = probatio.Schema({
        probatio.Required("name", description="The resource's name or host name, or part of it."): str,
        probatio.Optional("limit", description="Most resources to return.", default=10):
            probatio.All(int, probatio.Range(min=1, max=MAX_TOOL_ROWS)),
    })

    async def _run(self, hass, coordinators, args, llm_context):
        rows = []
        for c in coordinators:
            rows.extend({**resource_from(r), **_tenant(c, coordinators)}
                        for r in await c.source.client.find_resources(args["name"], args["limit"]))
        return {"total": len(rows), "resources": rows[: args["limit"]]}


class AlertAction(OpsRampTool):
    name = "opsramp_alert_action"
    title = "Act on an OpsRamp alert"
    description = ("Acknowledge, unacknowledge, suppress, unsuppress or close one OpsRamp alert. This changes the alert "
                   "in OpsRamp: use it only when the user asks for that action on that alert.")
    annotations = _WRITE
    parameters = probatio.Schema({
        probatio.Required("alert_id", description="The alert's id."): str,
        probatio.Required("action", description="What to do with the alert."): probatio.In(list(ALERT_ACTIONS)),
        probatio.Optional("comment", description="A note for the alert's history, for example why.", default=""): str,
    })

    async def _run(self, hass, coordinators, args, llm_context):
        data = {"alert_id": args["alert_id"].strip(), "action": args["action"], "comment": args["comment"]}
        await hass.services.async_call(DOMAIN, SERVICE_ALERT_ACTION, data, blocking=True, context=llm_context.context)
        return {"done": True, "alert_id": data["alert_id"], "action": data["action"]}


class OpsRampAPI(llm.API):
    """The tools; the action tool only while some entry allows alert actions."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        tools: list[llm.Tool] = [ListAlerts(), ListIncidents(), GetAlert(), FindResources()]
        prompt = API_PROMPT
        if any(c.allow_actions for c in _coordinators(self.hass)):
            tools.append(AlertAction())
            prompt += ACTION_PROMPT
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=tools)


@callback
def async_register_llm_api(hass: HomeAssistant) -> CALLBACK_TYPE:
    return llm.async_register_api(hass, OpsRampAPI(hass=hass, id=LLM_API_ID, name=LLM_API_NAME))
