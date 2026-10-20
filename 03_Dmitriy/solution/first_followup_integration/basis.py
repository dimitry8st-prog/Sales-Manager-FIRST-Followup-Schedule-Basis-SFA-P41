"""Pure projection into B8-06's existing schedule.basis contract.

The caller owns authentication, current evidence and timestamp provenance.
Original F103/F114/F108 APIs validate their records; nothing is persisted here.
"""

from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

import sales_first_decision as decision_api
import sales_outbound_intent as outbound_api
import sales_first_send_receipt as receipt_api


_SCOPE_FIELDS = frozenset(
    ("client_id", "campaign_id", "lead_id", "mailbox_ref", "thread_id")
)
_ORIGINAL_SCOPE_FIELDS = (
    "client_id", "runtime_campaign_id", "lead_id", "mailbox_ref", "thread_id"
)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class ScheduleBasisError(ValueError):
    """Contract failure containing only a public code, never input data."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise ScheduleBasisError(code)


def _check_json_domain(values):
    """Walk exact builtin containers without hooks, recursion or a depth cap.

    Active ancestors identify cycles; completed containers may be shared by
    multiple paths. Arbitrary extra original fields are checked, not removed.
    """
    active = set()
    complete = set()
    stack = [(value, False) for value in values]
    while stack:
        value, leaving = stack.pop()
        kind = type(value)
        if kind is str or kind is int or kind is bool or value is None:
            continue
        if kind is not dict and kind is not list:
            _fail("INVALID_INPUT")
        identity = id(value)
        if leaving:
            active.remove(identity)
            complete.add(identity)
            continue
        if identity in active:
            _fail("INVALID_INPUT")
        if identity in complete:
            continue
        active.add(identity)
        stack.append((value, True))
        if kind is dict:
            for key, child in value.items():
                if type(key) is not str:
                    _fail("INVALID_INPUT")
                stack.append((child, False))
        else:
            stack.extend((child, False) for child in value)


def _valid_id(value):
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _utc_microseconds(value):
    """Compare instants without overflowing datetime at aware now's edges."""
    offset = value.utcoffset()
    if offset is None:
        _fail("INVALID_INPUT")
    local = (
        ((value.toordinal() - 1) * 86400
         + value.hour * 3600 + value.minute * 60 + value.second) * 1000000
        + value.microsecond
    )
    return local - (
        (offset.days * 86400 + offset.seconds) * 1000000 + offset.microseconds
    )


def _validated_now(now):
    # Check the actual type hierarchy without consulting an object's custom
    # __class__ property. Ordinary datetime subclasses remain supported.
    if not issubclass(type(now), datetime):
        _fail("INVALID_INPUT")
    try:
        instant = _utc_microseconds(now)
        observed_at = now.isoformat()
    except Exception:
        raise ScheduleBasisError("INVALID_INPUT") from None
    return instant, observed_at


def _last_outbound(value, now_instant):
    if (
        type(value) is not str
        or _TIMESTAMP.fullmatch(value) is None
        or value.endswith("-00:00")
    ):
        _fail("SOURCE_UNCONFIRMED")
    if not value.endswith("Z"):
        # fromisoformat can normalize non-RFC3339 offsets such as +00:60.
        if int(value[-5:-3]) > 23 or int(value[-2:]) > 59:
            _fail("SOURCE_UNCONFIRMED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if _utc_microseconds(parsed) > now_instant:
            _fail("SOURCE_UNCONFIRMED")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ScheduleBasisError("SOURCE_UNCONFIRMED") from None


def project_schedule_basis(*, scope, policy_version, source_action_id, state, policy,
                           action, attempt, receipt, now):
    """Validate, correlate and project records without effects or new authority."""
    _check_json_domain(
        (scope, policy_version, source_action_id, state, policy, action, attempt,
         receipt)
    )
    if (
        type(scope) is not dict
        or scope.keys() != _SCOPE_FIELDS
        or not all(_valid_id(value) for value in scope.values())
        or not _valid_id(policy_version)
        or not _valid_id(source_action_id)
    ):
        _fail("INVALID_INPUT")
    try:
        if str(UUID(scope["campaign_id"])) != scope["campaign_id"]:
            _fail("INVALID_INPUT")
    except ValueError:
        raise ScheduleBasisError("INVALID_INPUT") from None

    now_instant, observed_at = _validated_now(now)
    if type(state) is not dict or type(policy) is not dict:
        _fail("DEPENDENCY_REJECTED")
    original_scope = {
        "client_id": scope["client_id"],
        "runtime_campaign_id": scope["campaign_id"],
        "lead_id": scope["lead_id"],
        "mailbox_ref": scope["mailbox_ref"],
        "thread_id": scope["thread_id"],
    }
    try:
        state_scope = {key: state[key] for key in _ORIGINAL_SCOPE_FIELDS}
    except KeyError:
        raise ScheduleBasisError("DEPENDENCY_REJECTED") from None
    if state_scope != original_scope:
        _fail("SCOPE_MISMATCH")
    if "sender_ref" in policy and policy["sender_ref"] != scope["mailbox_ref"]:
        _fail("SCOPE_MISMATCH")

    # F103 also validates timestamps. Enforce the bridge's stronger timestamp
    # contract first, keeping SOURCE_UNCONFIRMED for malformed/future evidence.
    if "last_outbound_at" not in state:
        _fail("DEPENDENCY_REJECTED")
    last_outbound = _last_outbound(state["last_outbound_at"], now_instant)
    event = {
        "schema_version": "FIRST_DIALOGUE_EVENT_V1",
        "event_id": "p41-validation",
        **state_scope,
        "kind": "AUTOREPLY",
        "observed_at": observed_at,
    }
    try:
        decision_api.decide_next_action(state, event, policy, now=now)
    except Exception:
        raise ScheduleBasisError("DEPENDENCY_REJECTED") from None
    if state["status"] in ("STOPPED", "HANDED_OFF"):
        _fail("FENCED")
    if state["status"] != "ACTIVE":
        _fail("DEPENDENCY_REJECTED")

    try:
        outbound_api.build_outbound_intent(action)
    except Exception:
        raise ScheduleBasisError("DEPENDENCY_REJECTED") from None
    if action["scope"] != original_scope:
        _fail("SCOPE_MISMATCH")
    if (
        action["action_id"] != source_action_id
        or action["policy_version"] != policy_version
        or policy["policy_version"] != policy_version
        or action["locale"] != policy["locale"]
        or action["language_pack_version"]
        != policy["language_pack_versions"][policy["locale"]]
    ):
        _fail("SCOPE_MISMATCH")
    if action["kind"] == "HANDOFF":
        _fail("SOURCE_UNCONFIRMED")

    try:
        normalized = receipt_api.normalize_send_receipt(attempt, receipt)
    except Exception:
        raise ScheduleBasisError("DEPENDENCY_REJECTED") from None
    if (
        any(normalized[key] != original_scope[key]
            for key in _ORIGINAL_SCOPE_FIELDS if key != "thread_id")
        or normalized["action_id"] != action["action_id"]
        or normalized["payload_sha256"] != action["payload_sha256"]
    ):
        _fail("SCOPE_MISMATCH")
    if normalized["state"] not in ("ACCEPTED", "DELIVERED"):
        _fail("SOURCE_UNCONFIRMED")

    # Exact int/nonnegative rules are already checked by F103 after the domain
    # guard. The addition below matches the consumer's normalized UTC basis.
    try:
        last_outbound + timedelta(seconds=policy["cooldown_seconds"])
    except (OverflowError, ValueError):
        raise ScheduleBasisError("SOURCE_UNCONFIRMED") from None
    return {
        "policy_version": policy_version,
        "source_action_id": source_action_id,
        "source_state": normalized["state"],
        "last_outbound_at": last_outbound.isoformat().replace("+00:00", "Z"),
        "max_followups": policy["max_followups"],
        "cooldown_seconds": policy["cooldown_seconds"],
        "followups_sent": state["followups_sent"],
    }
