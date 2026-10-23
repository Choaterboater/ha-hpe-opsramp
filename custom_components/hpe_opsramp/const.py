"""Constants for the HPE OpsRamp integration."""

DOMAIN = "hpe_opsramp"

CONF_BASE_URL = "base_url"
CONF_TENANT_ID = "tenant_id"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_SCAN = "scan_seconds"  # options: seconds between sweeps
CONF_ALLOW_ACTIONS = "allow_actions"  # options: acknowledge / suppress / close alerts from Home Assistant
DEFAULT_ALLOW_ACTIONS = False

MANUFACTURER = "HPE OpsRamp"

# On the tenant's API host (https://<tenant>.api.opsramp.com): the token endpoint and the tenant APIs.
TOKEN_PATH = "/tenancy/auth/oauth/token"
TENANTS_PATH = "/api/v2/tenants"

# What counts as open (OpsRamp's query strings and names).
ALERT_QUERY = "states:CRITICAL,WARNING"
OPEN_ALERT_STATES = ("CRITICAL", "WARNING")
INCIDENT_QUERY = "status:New,Open,Pending,On Hold"
RESOURCE_QUERY = "state:active"

# Alert actions: our name -> the action in OpsRamp's API path.
ALERT_ACTIONS = {
    "acknowledge": "acknowledge",
    "unacknowledge": "unAcknowledge",
    "suppress": "suppress",
    "unsuppress": "unSuppress",
    "close": "close",
}

# Account event entity: what it can fire.
EVENT_ALERT_OPENED = "alert_opened"
EVENT_ALERT_CLEARED = "alert_cleared"
EVENT_TYPES = [EVENT_ALERT_OPENED, EVENT_ALERT_CLEARED]

# OpsRamp allows a client tenant 50 paginated alert searches a minute; a sweep makes one
# alert search and one incident search, every POLL_SECONDS by default (MIN..MAX in the options).
POLL_SECONDS = 60
MIN_POLL_SECONDS = 30
MAX_POLL_SECONDS = 600
# The active resource count changes slowly: read at most this often.
RESOURCES_SECONDS = 300
# List attributes are capped at this many rows; Assist tools return at most MAX_TOOL_ROWS.
MAX_LIST_ROWS = 200
MAX_TOOL_ROWS = 50
# Short OpsRamp errors keep the last known state; only after this long without a good
# sweep (at least two sweeps) do the entities become unavailable.
UNAVAILABLE_AFTER_SECONDS = 300
# Backoff ceiling after 429 / 5xx answers.
MAX_BACKOFF_SECONDS = 300
# Every OpsRamp request gives up after this long, and requests are spaced this far apart.
REQUEST_TIMEOUT_SECONDS = 20
MIN_REQUEST_GAP_SECONDS = 0.35
# Search paging.
ALERT_PAGE_SIZE = 500
INCIDENT_PAGE_SIZE = 100
MAX_PAGES = 10
