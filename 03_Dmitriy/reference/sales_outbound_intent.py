"""F1-14: immutable FIRST outbound intent.

Validates one FIRST_ACTION_INTENT_V1, verifies the canonical payload hash and
returns a PREPARED FIRST_OUTBOUND_INTENT_V1. compare_intent decides whether a
proposed intent replays, differs from, or conflicts with a journaled one.

The module performs no I/O, sends nothing, keeps no state, reads no clock or
environment and never authorizes an external effect. Standard library plus the
accepted F109 handoff assembler only.
"""

import hashlib
import json
import math
import re
import uuid
from copy import deepcopy

from sales_first_handoff import HandoffContextError, assemble_handoff_context

ACTION_SCHEMA = "FIRST_ACTION_INTENT_V1"
INTENT_SCHEMA = "FIRST_OUTBOUND_INTENT_V1"
HANDOFF_INPUT_SCHEMA = "FIRST_HANDOFF_INPUT_V1"
HANDOFF_CONTEXT_SCHEMA = "FIRST_HANDOFF_CONTEXT_V1"

PREPARED = "PREPARED"
INITIAL_ATTEMPT_NO = 1

IDENTICAL = "IDENTICAL"
DISTINCT_ACTION = "DISTINCT_ACTION"

INVALID_INPUT = "INVALID_INPUT"
INVALID_PAYLOAD = "INVALID_PAYLOAD"
HASH_MISMATCH = "HASH_MISMATCH"
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
ACTION_PAYLOAD_CONFLICT = "ACTION_PAYLOAD_CONFLICT"

MAX_CANONICAL_PAYLOAD_BYTES = 16_777_216

_ID_MAX_LEN = 128
_FACT_IDS_MAX = 50
_SUBJECT_MAX_LEN = 2000
_BODY_MAX_LEN = 20000
_COMPARE_MAX_DEPTH = 32

_ACTION_FIELDS = frozenset(
    (
        "schema_version",
        "action_id",
        "scope",
        "expected_state_revision",
        "event_id",
        "kind",
        "policy_version",
        "locale",
        "language_pack_version",
        "template_id",
        "fact_ids",
        "payload",
        "payload_sha256",
        "binding_version",
    )
)
_ACTION_ID_FIELDS = (
    "action_id",
    "event_id",
    "policy_version",
    "locale",
    "language_pack_version",
    "binding_version",
)
_SCOPE_ID_FIELDS = ("client_id", "lead_id", "mailbox_ref")
_SCOPE_FIELDS = frozenset(_SCOPE_ID_FIELDS + ("runtime_campaign_id", "thread_id"))
_INTENT_FIELDS = frozenset(("schema_version", "action", "state", "attempt_no"))

_MAIL_KINDS = frozenset(("SEND", "REPLY", "FOLLOWUP"))
_KINDS = _MAIL_KINDS | frozenset(("HANDOFF",))
_MAIL_PAYLOAD_FIELDS = frozenset(("recipient_ref", "subject", "body"))
_HANDOFF_PAYLOAD_FIELDS = frozenset(
    ("recipient_ref", "handoff_id", "content_sha256", "context")
)
# Fields added by the F109 assembler on top of FIRST_HANDOFF_INPUT_V1.
_HANDOFF_ASSEMBLED_FIELDS = ("handoff_id", "content_sha256", "delivery_status")
_HANDOFF_SCOPE_FIELDS = (
    "client_id",
    "runtime_campaign_id",
    "lead_id",
    "mailbox_ref",
    "thread_id",
)
_HANDOFF_VERSION_FIELDS = ("policy_version", "locale", "language_pack_version")

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_HEX_LOWER = frozenset("0123456789abcdef")


class OutboundIntentError(ValueError):
    """Contract error. str(error) is the code only; input data is never included."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self._code = code

    @property
    def code(self) -> str:
        return self._code

    def __str__(self) -> str:
        return self._code


class _Uncomparable(Exception):
    """Internal: structure too deep to compare; never leaves this module."""


def _utf8_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_id(value) -> bool:
    if type(value) is not str:
        return False
    if not 1 <= len(value) <= _ID_MAX_LEN:
        return False
    if value != value.strip():
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return False
    return _utf8_encodable(value)


def _is_optional_id(value) -> bool:
    return value is None or _is_id(value)


def _is_uuid(value) -> bool:
    if type(value) is not str or _UUID_PATTERN.fullmatch(value) is None:
        return False
    return str(uuid.UUID(value)) == value


def _is_sha256(value) -> bool:
    return type(value) is str and len(value) == 64 and all(ch in _HEX_LOWER for ch in value)


def _is_revision(value) -> bool:
    return type(value) is int and value >= 0


def _is_fact_ids(value) -> bool:
    if type(value) is not list or len(value) > _FACT_IDS_MAX:
        return False
    if not all(_is_id(item) for item in value):
        return False
    return len(set(value)) == len(value)


def _is_body(value) -> bool:
    if type(value) is not str:
        return False
    if not 1 <= len(value) <= _BODY_MAX_LEN or not value.strip():
        return False
    if any(
        (ord(ch) < 0x20 and ch not in "\n\t") or ord(ch) == 0x7F for ch in value
    ):
        return False
    return _utf8_encodable(value)


def _is_subject(value) -> bool:
    return (
        type(value) is str
        and len(value) <= _SUBJECT_MAX_LEN
        and _utf8_encodable(value)
    )


def _scope_valid(scope) -> bool:
    if type(scope) is not dict or scope.keys() != _SCOPE_FIELDS:
        return False
    return (
        all(_is_id(scope[name]) for name in _SCOPE_ID_FIELDS)
        and _is_uuid(scope["runtime_campaign_id"])
        and _is_optional_id(scope["thread_id"])
    )


def _action_valid(action) -> bool:
    """INVALID_INPUT stage: every action field except payload contents."""
    if type(action) is not dict or action.keys() != _ACTION_FIELDS:
        return False
    if type(action["schema_version"]) is not str or action["schema_version"] != ACTION_SCHEMA:
        return False
    if not all(_is_id(action[name]) for name in _ACTION_ID_FIELDS):
        return False
    if not _scope_valid(action["scope"]):
        return False
    if not _is_revision(action["expected_state_revision"]):
        return False
    kind = action["kind"]
    if type(kind) is not str or kind not in _KINDS:
        return False
    if not _is_optional_id(action["template_id"]):
        return False
    if not _is_fact_ids(action["fact_ids"]):
        return False
    if not _is_sha256(action["payload_sha256"]):
        return False
    # Kind rules: thread_id null only for SEND; template_id null exactly for HANDOFF.
    if action["scope"]["thread_id"] is None and kind != "SEND":
        return False
    if (action["template_id"] is None) != (kind == "HANDOFF"):
        return False
    return True


def _mail_payload_valid(action: dict) -> bool:
    payload = action["payload"]
    if payload.keys() != _MAIL_PAYLOAD_FIELDS:
        return False
    return (
        _is_id(payload["recipient_ref"])
        and payload["recipient_ref"] == action["scope"]["lead_id"]
        and _is_subject(payload["subject"])
        and _is_body(payload["body"])
    )


def _reassembled_context(context: dict):
    """Rebuild FIRST_HANDOFF_INPUT_V1 and run the accepted F109 assembler."""
    if type(context) is not dict:
        return None
    if not all(name in context for name in _HANDOFF_ASSEMBLED_FIELDS):
        return None
    if type(context.get("schema_version")) is not str:
        return None
    if context["schema_version"] != HANDOFF_CONTEXT_SCHEMA:
        return None
    if type(context["delivery_status"]) is not str:
        return None
    if context["delivery_status"] != "NOT_DISPATCHED":
        return None
    # Shallow rebuild: the assembler validates first and never mutates input.
    handoff_input = {
        name: value
        for name, value in context.items()
        if name not in _HANDOFF_ASSEMBLED_FIELDS
    }
    handoff_input["schema_version"] = HANDOFF_INPUT_SCHEMA
    try:
        return assemble_handoff_context(handoff_input)
    except (HandoffContextError, ValueError, TypeError, RecursionError):
        # The accepted assembler hashes before returning; a lone surrogate
        # surfaces there as UnicodeEncodeError (a ValueError subclass).
        return None


def _handoff_payload_valid(action: dict) -> bool:
    payload = action["payload"]
    if payload.keys() != _HANDOFF_PAYLOAD_FIELDS:
        return False
    if not _is_id(payload["recipient_ref"]) or not _is_id(payload["handoff_id"]):
        return False
    if not _is_sha256(payload["content_sha256"]):
        return False
    context = payload["context"]
    assembled = _reassembled_context(context)
    # Full accepted context: ordered history/facts/materials, id, hash, status.
    if assembled is None or not _strict_equal(assembled, context, 0):
        return False
    scope = action["scope"]
    if any(context[name] != scope[name] for name in _HANDOFF_SCOPE_FIELDS):
        return False
    if any(context[name] != action[name] for name in _HANDOFF_VERSION_FIELDS):
        return False
    if not action["event_id"] == payload["handoff_id"] == context["handoff_id"]:
        return False
    if payload["content_sha256"] != context["content_sha256"]:
        return False
    context_fact_ids = [fact["fact_id"] for fact in context["facts"]]
    return sorted(action["fact_ids"]) == context_fact_ids


def _canonical_bytes(payload):
    """Exact canonical UTF-8 bytes, or None if the payload cannot be canonical."""
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        # ValueError covers NaN/Infinity, circular references and
        # UnicodeEncodeError (lone surrogates).
        return None


def _payload_valid(action: dict):
    """INVALID_PAYLOAD stage. Returns canonical bytes or None."""
    payload = action["payload"]
    if type(payload) is not dict:
        return None
    if action["kind"] in _MAIL_KINDS:
        valid = _mail_payload_valid(action)
    else:
        try:
            valid = _handoff_payload_valid(action)
        except _Uncomparable:
            valid = False
    if not valid:
        return None
    return _canonical_bytes(payload)


def _check_action(action) -> None:
    """Full FIRST_ACTION_INTENT_V1 check in normative error order."""
    if not _action_valid(action):
        raise OutboundIntentError(INVALID_INPUT)
    canonical = _payload_valid(action)
    if canonical is None:
        raise OutboundIntentError(INVALID_PAYLOAD)
    if hashlib.sha256(canonical).hexdigest() != action["payload_sha256"]:
        raise OutboundIntentError(HASH_MISMATCH)
    if len(canonical) > MAX_CANONICAL_PAYLOAD_BYTES:
        raise OutboundIntentError(PAYLOAD_TOO_LARGE)


def build_outbound_intent(action_input: dict) -> dict:
    """Return a new PREPARED FIRST_OUTBOUND_INTENT_V1 or raise OutboundIntentError.

    Error priority: INVALID_INPUT, INVALID_PAYLOAD, HASH_MISMATCH,
    PAYLOAD_TOO_LARGE. The input is never mutated or shared; nothing is sent.
    """
    _check_action(action_input)
    return {
        "schema_version": INTENT_SCHEMA,
        "action": deepcopy(action_input),
        "state": PREPARED,
        "attempt_no": INITIAL_ATTEMPT_NO,
    }


def _strict_equal(left, right, depth: int) -> bool:
    """Type-exact structural equality (1 != True != 1.0); no data is changed."""
    if depth > _COMPARE_MAX_DEPTH:
        raise _Uncomparable()
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if left.keys() != right.keys():
            return False
        return all(_strict_equal(left[k], right[k], depth + 1) for k in left)
    if type(left) is list:
        if len(left) != len(right):
            return False
        return all(_strict_equal(a, b, depth + 1) for a, b in zip(left, right))
    if type(left) is float:
        return left == right or (math.isnan(left) and math.isnan(right))
    if type(left) in (str, int, bool, type(None)):
        return left == right
    return left is right


def _journal_key(intent):
    """(client_id, runtime_campaign_id, mailbox_ref, action_id) or None."""
    if type(intent) is not dict:
        return None
    action = intent.get("action")
    if type(action) is not dict:
        return None
    scope = action.get("scope")
    if type(scope) is not dict:
        return None
    client_id = scope.get("client_id")
    campaign_id = scope.get("runtime_campaign_id")
    mailbox_ref = scope.get("mailbox_ref")
    action_id = action.get("action_id")
    if not (
        _is_id(client_id)
        and _is_uuid(campaign_id)
        and _is_id(mailbox_ref)
        and _is_id(action_id)
    ):
        return None
    return (client_id, campaign_id, mailbox_ref, action_id)


def _intent_valid(intent) -> bool:
    if type(intent) is not dict or intent.keys() != _INTENT_FIELDS:
        return False
    if type(intent["schema_version"]) is not str or intent["schema_version"] != INTENT_SCHEMA:
        return False
    if type(intent["state"]) is not str or intent["state"] != PREPARED:
        return False
    if type(intent["attempt_no"]) is not int or intent["attempt_no"] != INITIAL_ATTEMPT_NO:
        return False
    try:
        _check_action(intent["action"])
    except OutboundIntentError:
        return False
    return True


def compare_intent(existing: dict, proposed: dict) -> str:
    """Return IDENTICAL or DISTINCT_ACTION, or raise OutboundIntentError.

    Error priority: ACTION_PAYLOAD_CONFLICT, INVALID_INPUT. A conflict is
    raised whenever both journal keys are well-formed and equal but the full
    intents differ. Otherwise both intents must be exact valid
    FIRST_OUTBOUND_INTENT_V1 objects; malformed input is never DISTINCT_ACTION.
    IDENTICAL never authorizes a provider call.
    """
    existing_key = _journal_key(existing)
    proposed_key = _journal_key(proposed)
    if existing_key is not None and existing_key == proposed_key:
        try:
            same = _strict_equal(existing, proposed, 0)
        except _Uncomparable:
            raise OutboundIntentError(INVALID_INPUT) from None
        if not same:
            raise OutboundIntentError(ACTION_PAYLOAD_CONFLICT)
    if not _intent_valid(existing) or not _intent_valid(proposed):
        raise OutboundIntentError(INVALID_INPUT)
    if existing_key != proposed_key:
        return DISTINCT_ACTION
    return IDENTICAL
