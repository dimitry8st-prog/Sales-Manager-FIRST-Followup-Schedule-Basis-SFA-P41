"""B8-06 R3 follow-up scheduling facade.

The supplied ports own authentication, scoped transactions, and all source
records. This module stores only its schedules and replay metadata. It never
calls a provider; a due follow-up can only be prepared through dispatch.propose.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import traceback
import uuid
from typing import Any


SCOPE_FIELDS = (
    "client_id",
    "campaign_id",
    "lead_id",
    "mailbox_ref",
    "thread_id",
)
OPERATIONS = {
    "schedule": {"scope", "policy_version", "source_action_id", "ordinal", "due_at"},
    "tick": {"scope", "schedule_id"},
    "cancel": {"scope", "reason"},
}
F103_ACTIONS = {
    "NOOP",
    "STOP",
    "HANDOFF",
    "PROPOSE_REPLY",
    "PROPOSE_FOLLOWUP",
    "QUARANTINE",
}
FENCE_ACTIONS = {"STOP", "HANDOFF", "QUARANTINE"}
IDENTIFIER_LIMIT = 128
_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_LOGGER = logging.getLogger(__name__)
BASIS_FIELDS = {
    "policy_version",
    "source_action_id",
    "source_state",
    "last_outbound_at",
    "max_followups",
    "cooldown_seconds",
    "followups_sent",
}


def _identifier(value: Any) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= IDENTIFIER_LIMIT
        and value.strip() == value
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _valid_scope(value: Any) -> bool:
    if type(value) is not dict or set(value) != set(SCOPE_FIELDS):
        return False
    if not all(_identifier(value[field]) for field in SCOPE_FIELDS):
        return False
    try:
        return str(uuid.UUID(value["campaign_id"])) == value["campaign_id"]
    except (ValueError, AttributeError, TypeError):
        return False


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _parse_timestamp(value: Any) -> dt.datetime:
    if type(value) is not str or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be RFC3339 with an explicit offset")
    if value.endswith("-00:00"):
        raise ValueError("timestamp offset -00:00 is unspecified")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def _valid_timestamp(value: Any) -> bool:
    try:
        _parse_timestamp(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _format_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _result(status: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "data": data if data is not None else {}}


class CommitOutcomeUnknown(RuntimeError):
    """The adapter lost the commit acknowledgement after sending COMMIT."""


class Service:
    """Execute schedule, tick, and cancel within the supplied scoped port."""

    def __init__(self, ports: Any) -> None:
        self._ports = ports

    def execute(self, operation: Any, request: Any) -> dict[str, Any]:
        if type(operation) is not str or operation not in OPERATIONS:
            return _result("INVALID")
        if type(request) is not dict or set(request) != OPERATIONS[operation]:
            return _result("INVALID", {"code": "INVALID_REQUEST"})

        scope = request["scope"]
        if not _valid_scope(scope):
            return _result("INVALID")
        if not self._valid_operation_request(operation, request):
            return _result("INVALID")
        auth_status = self._authorization_status(scope)
        if auth_status is not None:
            return _result(auth_status)

        try:
            # The production transaction adapter must acquire the shared locks
            # in authority -> task/lease -> schedule -> journal order and scope
            # every bucket read/write to this exact LeadScope.
            with self._ports.transaction(scope):
                if operation == "schedule":
                    result = self._schedule(scope, request)
                elif operation == "tick":
                    result = self._tick(scope, request)
                else:
                    result = self._cancel(scope, request)
            return result
        except CommitOutcomeUnknown:
            identity = self._stable_operation_identity(operation, scope, request)
            self._scoped_readback(operation, scope, identity)
            return _result("UNKNOWN", identity)
        except PermissionError:
            return _result("UNAUTHORIZED")
        except Exception as exc:
            # Keep the public result fail-closed while retaining a traceback
            # without logging exception text that could contain adapter secrets.
            _LOGGER.error(
                "B8-06 operation failed; operation=%s; exception_type=%s\n%s",
                operation,
                type(exc).__name__,
                "\n".join(
                    f"{frame.filename}:{frame.lineno}:{frame.name}"
                    for frame in traceback.extract_tb(exc.__traceback__)
                ),
            )
            return _result("DEPENDENCY_UNAVAILABLE")

    @staticmethod
    def _stable_operation_identity(
        operation: str, scope: dict[str, str], request: dict[str, Any]
    ) -> dict[str, str]:
        if operation == "schedule":
            schedule_id = _sha256(
                [scope, request["policy_version"], request["source_action_id"], request["ordinal"]]
            )
            return {"schedule_id": schedule_id}
        if operation == "tick":
            return {"schedule_id": request["schedule_id"]}
        return {"cancel_id": _sha256([scope, "cancel", request["reason"]])}

    def _scoped_readback(
        self, operation: str, scope: dict[str, str], identity: dict[str, str]
    ) -> None:
        """Re-read durable state in scope; keep UNKNOWN because COMMIT was unconfirmed."""
        # Connection and statement timeouts belong to the supplied transaction
        # adapter's explicit policy; recovery must use that same bounded policy.
        try:
            with self._ports.transaction(scope):
                if operation in {"schedule", "tick"}:
                    self._ports.get("schedules", identity["schedule_id"])
                else:
                    self._ports.get("schedules_control", "current")
                    self._ports.scan("schedules")
        except Exception as exc:
            # The caller still receives UNKNOWN. Do not turn a failed recovery
            # read into success or include adapter exception text in logs.
            _LOGGER.error(
                "B8-06 uncertain-commit readback failed; operation=%s; exception_type=%s",
                operation,
                type(exc).__name__,
            )

    @staticmethod
    def _valid_operation_request(operation: str, request: dict[str, Any]) -> bool:
        if operation == "schedule":
            return (
                _identifier(request["policy_version"])
                and _identifier(request["source_action_id"])
                and type(request["ordinal"]) is int
                and request["ordinal"] >= 0
                and _valid_timestamp(request["due_at"])
            )
        if operation == "tick":
            return _identifier(request["schedule_id"])
        return type(request["reason"]) is str and request["reason"] in {
            "STOP",
            "HANDOFF",
            "UNKNOWN",
        }

    def _authorization_status(self, scope: dict[str, str]) -> str | None:
        principal = getattr(self._ports, "auth", None)
        if type(principal) is not dict or principal.get("authenticated") is not True:
            return "UNAUTHENTICATED"
        capabilities = principal.get("capabilities")
        # Authentication supplies canonical capability tokens; malformed
        # whitespace variants fail closed instead of being normalized to access.
        if (
            type(capabilities) not in (list, tuple)
            or principal.get("client_id") != scope["client_id"]
            or ("*" not in capabilities and "schedule.manage" not in capabilities)
        ):
            return "UNAUTHORIZED"
        return None

    def _is_fenced(self) -> bool:
        control = self._ports.get("schedules_control", "current")
        if control is not None:
            if (
                type(control) is not dict
                or type(control.get("cancelled")) is not bool
                or type(control.get("reason")) is not str
                or control.get("reason") not in {"STOP", "HANDOFF", "UNKNOWN", "QUARANTINE"}
            ):
                return True
            if control.get("cancelled") is True:
                return True

        authority = self._ports.get("authority", "current")
        # Only an explicit ACTIVE authority projection permits new scheduling
        # work. Unknown or incomplete state fails closed.
        return type(authority) is not dict or authority.get("status") != "ACTIVE"

    def _schedule(self, scope: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        policy_version = request["policy_version"]
        source_action_id = request["source_action_id"]
        ordinal = request["ordinal"]
        if not _identifier(policy_version) or not _identifier(source_action_id):
            return _result("INVALID")
        if type(ordinal) is not int or ordinal < 0:
            return _result("INVALID")
        try:
            due_at = _parse_timestamp(request["due_at"])
        except (TypeError, ValueError, OverflowError):
            return _result("INVALID")

        identity = [scope, policy_version, source_action_id, ordinal]
        schedule_id = _sha256(identity)
        request_hash = _sha256([*identity, _format_timestamp(due_at)])

        # Replay is resolved from the immutable identity before consulting the
        # current F103 projection, whose receipt counters may have advanced.
        previous = self._ports.get("schedules", schedule_id)
        if previous is not None:
            if type(previous) is not dict:
                return _result("DEPENDENCY_UNAVAILABLE")
            if (
                previous.get("policy_version") != policy_version
                or previous.get("source_action_id") != source_action_id
                or type(previous.get("ordinal")) is not int
                or previous.get("ordinal") != ordinal
            ):
                return _result("DEPENDENCY_UNAVAILABLE")
            stored_scope_key = previous.get("scope_key")
            if stored_scope_key is not None and stored_scope_key != _canonical(scope):
                return _result("DEPENDENCY_UNAVAILABLE")
            previous_state = previous.get("state")
            if type(previous_state) is not str or previous_state not in {
                "PENDING", "PROPOSED", "CANCELLED"
            }:
                return _result("DEPENDENCY_UNAVAILABLE")
            previous_action_id = previous.get("action_id")
            previous_payload_ref = previous.get("payload_ref")
            if previous_state == "PROPOSED":
                if (
                    previous_action_id != _sha256([scope, schedule_id, "FOLLOWUP"])
                    or not _identifier(previous_payload_ref)
                ):
                    return _result("DEPENDENCY_UNAVAILABLE")
            elif previous_action_id is not None or previous_payload_ref is not None:
                return _result("DEPENDENCY_UNAVAILABLE")
            if not _valid_sha256(previous.get("request_hash")):
                return _result("DEPENDENCY_UNAVAILABLE")
            try:
                previous_due_at = _format_timestamp(
                    _parse_timestamp(previous.get("due_at"))
                )
            except (TypeError, ValueError, OverflowError):
                return _result("DEPENDENCY_UNAVAILABLE")
            if previous["request_hash"] != _sha256([*identity, previous_due_at]):
                return _result("DEPENDENCY_UNAVAILABLE")
            if previous.get("request_hash") != request_hash:
                return _result("CONFLICT")
            return _result(
                "REPLAY",
                {"schedule_id": schedule_id, "state": previous["state"]},
            )

        if self._is_fenced():
            return _result("BLOCKED")

        basis = self._ports.call(
            "schedule.basis",
            {
                "scope": scope,
                "policy_version": policy_version,
                "source_action_id": source_action_id,
            },
        )
        if type(basis) is not dict or set(basis) != BASIS_FIELDS:
            return _result("DEPENDENCY_UNAVAILABLE")
        if (
            basis["policy_version"] != policy_version
            or basis["source_action_id"] != source_action_id
            or not _identifier(basis["policy_version"])
            or not _identifier(basis["source_action_id"])
            or type(basis["source_state"]) is not str
            or basis["source_state"] not in {"DELIVERED", "ACCEPTED"}
        ):
            return _result("DEPENDENCY_UNAVAILABLE")

        limit = basis["max_followups"]
        cooldown = basis["cooldown_seconds"]
        sent = basis["followups_sent"]
        if any(type(value) is not int or value < 0 for value in (limit, cooldown, sent)):
            return _result("DEPENDENCY_UNAVAILABLE")
        try:
            last_outbound_at = _parse_timestamp(basis["last_outbound_at"])
            earliest_due = last_outbound_at + dt.timedelta(seconds=cooldown)
        except (TypeError, ValueError, OverflowError):
            return _result("DEPENDENCY_UNAVAILABLE")
        if ordinal != sent or ordinal >= limit or due_at < earliest_due:
            return _result("INVALID")

        row = {
            "state": "PENDING",
            "due_at": _format_timestamp(due_at),
            "ordinal": ordinal,
            "policy_version": policy_version,
            "source_action_id": source_action_id,
            "request_hash": request_hash,
            "scope_key": _canonical(scope),
        }
        self._ports.put("schedules", schedule_id, row)
        return _result("OK", {"schedule_id": schedule_id, "state": "PENDING"})

    def _cancel_pending(self, reason: str) -> int:
        control = self._ports.get("schedules_control", "current")
        if control is not None and (
            type(control) is not dict
            or type(control.get("cancelled")) is not bool
            or type(control.get("reason")) is not str
            or control.get("reason") not in {"STOP", "HANDOFF", "UNKNOWN", "QUARANTINE"}
        ):
            raise ValueError("malformed schedule control row")
        if control is None or control["cancelled"] is not True:
            self._ports.put(
                "schedules_control",
                "current",
                {"cancelled": True, "reason": reason},
            )

        changed = 0
        for schedule_id, row in self._ports.scan("schedules"):
            if type(row) is not dict:
                raise ValueError("malformed schedule row")
            if row.get("state") == "PENDING":
                updated = dict(row)
                updated["state"] = "CANCELLED"
                self._ports.put("schedules", schedule_id, updated)
                changed += 1
        return changed

    def _cancel(self, _scope: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        reason = request["reason"]
        if type(reason) is not str or reason not in {"STOP", "HANDOFF", "UNKNOWN"}:
            return _result("INVALID")
        return _result("OK", {"cancelled": self._cancel_pending(reason)})

    def _tick(self, scope: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        schedule_id = request["schedule_id"]
        if not _identifier(schedule_id):
            return _result("INVALID")
        row = self._ports.get("schedules", schedule_id)
        if row is None:
            if self._is_fenced():
                return _result("BLOCKED")
            return _result("NOT_FOUND")
        if type(row) is not dict:
            return _result("DEPENDENCY_UNAVAILABLE")
        state = row.get("state")
        if type(state) is not str or state not in {"PENDING", "PROPOSED", "CANCELLED"}:
            return _result("DEPENDENCY_UNAVAILABLE")

        try:
            due_at = _parse_timestamp(row["due_at"])
            for field in ("ordinal", "policy_version", "source_action_id"):
                if field not in row:
                    return _result("DEPENDENCY_UNAVAILABLE")
            if (
                type(row["ordinal"]) is not int
                or row["ordinal"] < 0
                or not _identifier(row["policy_version"])
                or not _identifier(row["source_action_id"])
            ):
                return _result("DEPENDENCY_UNAVAILABLE")
            identity = [scope, row["policy_version"], row["source_action_id"], row["ordinal"]]
            scope_key = row.get("scope_key")
            if scope_key is not None:
                if type(scope_key) is not str or scope_key != _canonical(scope):
                    return _result("DEPENDENCY_UNAVAILABLE")
                if schedule_id != _sha256(identity):
                    return _result("DEPENDENCY_UNAVAILABLE")
            stored_request_hash = row.get("request_hash")
            if stored_request_hash is not None:
                expected_request_hash = _sha256(
                    [*identity, _format_timestamp(due_at)]
                )
                if (
                    not _valid_sha256(stored_request_hash)
                    or stored_request_hash != expected_request_hash
                ):
                    return _result("DEPENDENCY_UNAVAILABLE")
            elif scope_key is not None:
                return _result("DEPENDENCY_UNAVAILABLE")

            action_id = row.get("action_id")
            payload_ref = row.get("payload_ref")
            if state == "PROPOSED":
                expected_action_id = _sha256([scope, schedule_id, "FOLLOWUP"])
                if (
                    action_id != expected_action_id
                    or not _identifier(payload_ref)
                ):
                    return _result("DEPENDENCY_UNAVAILABLE")
            elif action_id is not None or payload_ref is not None:
                return _result("DEPENDENCY_UNAVAILABLE")

            now = self._ports.now
            if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
                return _result("INVALID")
            now = now.astimezone(dt.timezone.utc)
        except (TypeError, ValueError, KeyError, OverflowError):
            return _result("DEPENDENCY_UNAVAILABLE")

        if state == "CANCELLED":
            return _result("OK", {"schedule_id": schedule_id, "state": "CANCELLED"})
        if state == "PROPOSED":
            return _result("REPLAY", {"schedule_id": schedule_id, "state": "PROPOSED"})
        if self._is_fenced():
            return _result("BLOCKED")

        if now < due_at:
            return _result("OK", {"schedule_id": schedule_id, "state": "NOT_DUE"})

        schedule = {
            key: row[key]
            for key in ("state", "due_at", "ordinal", "policy_version", "source_action_id")
        }
        # Keep this dependency call inside the scoped transaction. The
        # production adapter must retain its transaction-scoped schedule lock
        # through commit; releasing it here would let STOP commit between the
        # fence check and dispatch preparation.
        decision = self._ports.call(
            "dialogue.followup",
            {"scope": scope, "schedule": schedule},
        )
        if type(decision) is not dict or set(decision) != {"action", "proposal"}:
            return _result("DEPENDENCY_UNAVAILABLE")
        action = decision["action"]
        proposal = decision["proposal"]
        if type(action) is not str or action not in F103_ACTIONS:
            return _result("DEPENDENCY_UNAVAILABLE")

        if action == "NOOP":
            if proposal is not None:
                return _result("DEPENDENCY_UNAVAILABLE")
            return _result("OK", {"schedule_id": schedule_id, "state": "NOT_DUE"})

        if action in FENCE_ACTIONS:
            if proposal is not None:
                return _result("DEPENDENCY_UNAVAILABLE")
            self._cancel_pending(action)
            return _result("OK", {"schedule_id": schedule_id, "state": "CANCELLED"})

        if action != "PROPOSE_FOLLOWUP":
            return _result("DEPENDENCY_UNAVAILABLE")
        if (
            type(proposal) is not dict
            or set(proposal) != {"action_id", "payload_ref"}
            or not _identifier(proposal.get("action_id"))
            or not _identifier(proposal.get("payload_ref"))
        ):
            return _result("DEPENDENCY_UNAVAILABLE")

        action_id = _sha256([scope, schedule_id, "FOLLOWUP"])
        if proposal["action_id"] != action_id:
            return _result("DEPENDENCY_UNAVAILABLE")
        dispatch = self._ports.call(
            "dispatch.propose",
            {
                "scope": scope,
                "action_id": action_id,
                "kind": "FOLLOWUP",
                "payload_ref": proposal["payload_ref"],
            },
        )
        if (
            type(dispatch) is not dict
            or set(dispatch) != {"action_id", "state"}
            or dispatch["action_id"] != action_id
            or dispatch["state"] != "PREPARED"
        ):
            return _result("DEPENDENCY_UNAVAILABLE")

        updated = dict(row)
        updated.update(
            {
                "state": "PROPOSED",
                "action_id": action_id,
                "payload_ref": proposal["payload_ref"],
            }
        )
        replay_key = _canonical([scope, "tick", schedule_id])
        replay_response = {
            "status": "OK",
            "data": {"schedule_id": schedule_id, "state": "PROPOSED"},
        }
        self._ports.put("schedules", schedule_id, updated)
        self._ports.put(
            "replay",
            replay_key,
            {
                "request_hash": _sha256([scope, "tick", schedule_id]),
                "response": replay_response,
            },
        )
        return _result("OK", {"schedule_id": schedule_id, "state": "PROPOSED"})


def create_service(ports: Any) -> Service:
    """Build the B8-06 facade using authenticated, scoped adapters."""
    return Service(ports)
