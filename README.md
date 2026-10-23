# HPE OpsRamp for Home Assistant (unofficial)

> Not affiliated with or endorsed by Hewlett Packard Enterprise (HPE). "HPE
> OpsRamp" is used only to name the service this integration talks to. No HPE or
> OpsRamp logos are included; the icon is original artwork.

Brings an HPE OpsRamp tenant into Home Assistant: open alerts and incidents as
sensors, an event when an alert opens or clears, and tools for Assist so you can
ask a conversation agent "what critical OpsRamp alerts are open?". It is a sibling
of the **HPE Central**, **HPE Mist** and **HPE UXI** integrations.

**It only reads OpsRamp until you allow alert actions** in its options (off by
default). One config entry per OpsRamp tenant gives you a tenant device with:

- *Open alerts*: alerts in CRITICAL or WARNING that are not closed. Attribute
  `alerts` (id, state, subject, resource, priority, acknowledged, created),
  critical first, newest first.
- *Critical alerts*, *Warning alerts*, *Unacknowledged alerts* (neither
  acknowledged nor suppressed)
- *Critical alert*: a problem sensor, on while any critical alert is open
- *Open incidents*: New, Open, Pending or On Hold, created in the last 30 days
  (OpsRamp's default window). Attribute `incidents`. Unknown if the key may not
  read tickets.
- *Active resources*: how many resources OpsRamp lists as active
- an **Events** entity

List attributes are capped at 200 rows and kept out of the recorder.

### Events

| Event type | Data |
|---|---|
| `alert_opened`, `alert_cleared` | id, state, subject, resource, priority, created |

Nothing fires on the first check after start, so a restart does not replay old alerts.

### Assist tools

Under your conversation agent's options, tick **HPE OpsRamp** next to (or instead
of) *Assist* under *Control Home Assistant*. The agent then gets:

| Tool | Does |
|---|---|
| `opsramp_list_alerts` | Open alerts from the last check; filter by state, not acknowledged, or text |
| `opsramp_list_incidents` | Open incidents from the last check |
| `opsramp_get_alert` | One alert's full details (asks OpsRamp) |
| `opsramp_find_resources` | Resources by name or host name (asks OpsRamp) |
| `opsramp_alert_action` | Acknowledge, unacknowledge, suppress, unsuppress or close one alert. **Only offered while alert actions are allowed**; refused when a non-admin user is talking. |

### Alert actions

Off by default. With *Allow alert actions from Home Assistant* on, admin users, automations and scripts can call
(Home Assistant refuses it for non-admin users):

```yaml
action: hpe_opsramp.alert_action
data:
  alert_id: "12345678"
  action: acknowledge   # unacknowledge, suppress, unsuppress, close
  comment: Looking at it
```

The alert ids are in the *Open alerts* sensor's `alerts` attribute. After an
action the integration checks OpsRamp again straight away.

## Install

1. HACS → ⋮ → Custom repositories → `https://github.com/Choaterboater/ha-hpe-opsramp`, type *Integration*.
2. Download **HPE OpsRamp**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **HPE OpsRamp**.

## Set up

In OpsRamp: Setup → Account → Integrations, add a **Custom Integration** for your
client, set its inbound authentication to **OAUTH2** with a role that can read
alerts, resources and tickets, and generate a key. Then enter:

- **API host**: your tenant's API host, for example `https://acme.api.opsramp.com`
- **Tenant ID**: the client (or partner) id shown with the key
- **Key** and **Secret**

The secret is stored only in the config entry and is never logged. Give the key
the smallest role that works; only OpsRamp decides what it may do.

Options: *Check OpsRamp every (seconds)*, 60 by default (30 to 600), and *Allow
alert actions from Home Assistant*.

## How it reads OpsRamp

- Tokens come from `<API host>/tenancy/auth/oauth/token` (client credentials),
  are reused until a minute before they expire (2 hours), and renewed once on a 401.
- Every check makes one alert search (`queryString=states:CRITICAL,WARNING`) and one
  incident search; the active resource count is read every 5 minutes. A client
  tenant may make 50 paginated alert searches a minute, so the default leaves room
  for other tools on the same tenant. Requests are spaced at least 0.35 s apart.
- On 429 (OpsRamp's `x-ratelimit-reset` is honoured) or 5xx the checks back off,
  up to 5 minutes. Through short trouble the last state is kept; after 5 minutes
  without a good check the entities become unavailable.

## Limits

- OpsRamp's Copilot chat has no public API, so the Assist tools use OpsRamp's
  REST API instead; your own conversation agent does the talking.
- Alerts are searched up to 5,000 (10 pages of 500).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

The tests use a fake OpsRamp with sample data, not from a real network.
