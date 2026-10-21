import re
from datetime import datetime, timezone

from .resource_policy import RESOURCE_ACTION_TTL_SECONDS, RESOURCE_ACTIONS

ACTION_KINDS = [key for key, _label in RESOURCE_ACTIONS]
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_KEY_SAFE = re.compile(r"^[A-Za-z0-9_.:\[\]+\-=]{4,1024}$")
PROCESS_IDENTITY_FIELDS = ("key", "pid", "start_ticks", "namespace", "uid", "exe_identity")
CHAT_MAX_CHARS = 4000


def is_uuid(value):
    return isinstance(value, str) and bool(_UUID.match(value))


def valid_resource_key(value):
    return isinstance(value, str) and bool(_KEY_SAFE.match(value))


def valid_action_kind(value):
    return value in ACTION_KINDS


def action_expiry(now=None):
    now = now or datetime.now(timezone.utc)
    return now.timestamp() + RESOURCE_ACTION_TTL_SECONDS, now


def parse_expiry(value, now=None):
    if not isinstance(value, str) or not value.strip():
        return None, "expiry is required"
    try:
        due = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None, "expiry is not a timestamp"
    if due.tzinfo is None:
        return None, "expiry carries no timezone"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return due, ("expired" if due <= now else None)


def validate_expected_process(expected):
    if not isinstance(expected, dict):
        return "expected identity is required"
    for field in PROCESS_IDENTITY_FIELDS:
        if field not in expected:
            return "expected identity lacks %s" % field
    if type(expected["pid"]) is not int or not 1 <= expected["pid"] <= 2147483647:
        return "expected pid is invalid"
    start = expected["start_ticks"]
    if (not isinstance(start, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", start)
            or len(start) > 20 or int(start) > 18446744073709551615):
        return "expected start_ticks is invalid"
    if type(expected["uid"]) is not int or not 0 <= expected["uid"] <= 4294967294:
        return "expected uid is invalid"
    if not isinstance(expected["key"], str) or not valid_resource_key(expected["key"]):
        return "expected key is invalid"
    if not isinstance(expected["namespace"], str) or not expected["namespace"]:
        return "expected namespace is invalid"
    if not isinstance(expected["exe_identity"], str) or not expected["exe_identity"]:
        return "expected exe identity is invalid"
    return None


def expected_from_row(row):
    return {field: row.get(field) for field in PROCESS_IDENTITY_FIELDS}
