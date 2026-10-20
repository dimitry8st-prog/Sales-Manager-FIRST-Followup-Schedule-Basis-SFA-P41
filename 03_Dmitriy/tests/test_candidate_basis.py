"""Contract regressions for the pure P41 schedule-basis projection.

All records below are synthetic. Dependency tests execute the supplied public
F103/F114/F108 APIs; consumer tests execute the supplied public B8-06 service.
No test claims authenticated/connected runtime or timestamp provenance.
"""

import ast
import builtins
import copy
import datetime as dt
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from common import Ports, fixture
import b806_api
import sales_first_decision as decision_api
import sales_first_send_receipt as receipt_api
import sales_outbound_intent as outbound_api
from sales_first_handoff import assemble_handoff_context
import first_followup_integration.basis as basis_api


ROOT = Path(__file__).resolve().parents[1]
CODES = {
    "INVALID_INPUT", "DEPENDENCY_REJECTED", "SCOPE_MISMATCH",
    "SOURCE_UNCONFIRMED", "FENCED",
}


def rehash_action(inputs):
    """Use the original canonical payload format after a deliberate test edit."""
    raw = json.dumps(
        inputs["action"]["payload"], ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    for record in (inputs["action"], inputs["attempt"], inputs["receipt"]):
        if record is not None:
            record["payload_sha256"] = digest


def change_scope(inputs, field, value):
    """Change a complete linked synthetic identity, not a foreign-record case."""
    inputs["scope"][field] = value
    original = "runtime_campaign_id" if field == "campaign_id" else field
    inputs["state"][original] = value
    inputs["action"]["scope"][original] = value
    if field != "thread_id":
        inputs["attempt"][original] = value
        if inputs["receipt"] is not None:
            inputs["receipt"][original] = value
    if field == "mailbox_ref":
        inputs["policy"]["sender_ref"] = value
    if field == "lead_id":
        inputs["action"]["payload"]["recipient_ref"] = value
        rehash_action(inputs)


def handoff_fixture():
    inputs = fixture()
    scope = inputs["action"]["scope"]
    policy = inputs["policy"]
    context = assemble_handoff_context({
        "schema_version": "FIRST_HANDOFF_INPUT_V1", **scope,
        "handoff_event_id": "handoff-test",
        "product_id": policy["product_id"],
        "policy_version": policy["policy_version"],
        "locale": policy["locale"],
        "language_pack_version": inputs["action"]["language_pack_version"],
        "status": "ACTIVE", "reason": "INTEREST",
        "language_packs": copy.deepcopy(policy["language_pack_versions"]),
        "summary": "Synthetic summary", "next_step": "Synthetic next step",
        "history": [{
            **scope, "message_id": "message-test", "sequence": 0,
            "direction": "INBOUND", "actor": "BUYER",
            "body": "Synthetic history", "fact_ids": [], "material_ids": [],
        }],
        "facts": [], "materials": [],
    })
    inputs["action"].update(
        kind="HANDOFF", template_id=None, event_id=context["handoff_id"],
        fact_ids=[], payload={
            "recipient_ref": scope["lead_id"],
            "handoff_id": context["handoff_id"],
            "content_sha256": context["content_sha256"],
            "context": context,
        },
    )
    rehash_action(inputs)
    return inputs


class CandidateBasisTests(unittest.TestCase):
    def go(self, inputs=None):
        return basis_api.project_schedule_basis(**(
            fixture() if inputs is None else inputs
        ))

    def reject(self, inputs, code=None):
        with self.assertRaises(basis_api.ScheduleBasisError) as caught:
            self.go(inputs)
        error = caught.exception
        self.assertIsInstance(error, ValueError)
        self.assertIn(error.code, CODES)
        self.assertEqual(str(error), error.code)
        self.assertEqual(error.args, (error.code,))
        if code is not None:
            self.assertEqual(error.code, code)
        return error

    def request(self, inputs, due_at="2026-10-20T11:00:00Z", ordinal=None):
        return {
            "scope": copy.deepcopy(inputs["scope"]),
            "policy_version": inputs["policy_version"],
            "source_action_id": inputs["source_action_id"],
            "ordinal": inputs["state"]["followups_sent"] if ordinal is None else ordinal,
            "due_at": due_at,
        }

    def test_public_signature_has_only_keyword_inputs(self):
        parameters = inspect.signature(basis_api.project_schedule_basis).parameters
        self.assertEqual(tuple(parameters), (
            "scope", "policy_version", "source_action_id", "state", "policy",
            "action", "attempt", "receipt", "now",
        ))
        for parameter in parameters.values():
            self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_exact_fixture_and_seven_fields(self):
        expected = json.loads((ROOT / "fixtures/expected.json").read_text())
        actual = self.go()
        self.assertIs(type(actual), dict)
        self.assertEqual(actual, expected)
        self.assertEqual(set(actual), b806_api.BASIS_FIELDS)

    def test_real_public_dependencies_and_validation_event(self):
        inputs = fixture()
        with (
            patch.object(decision_api, "decide_next_action", wraps=decision_api.decide_next_action) as decision,
            patch.object(outbound_api, "build_outbound_intent", wraps=outbound_api.build_outbound_intent) as outbound,
            patch.object(receipt_api, "normalize_send_receipt", wraps=receipt_api.normalize_send_receipt) as receipt,
        ):
            self.go(inputs)
        decision.assert_called_once()
        state, event, policy = decision.call_args.args
        self.assertEqual(state, inputs["state"])
        self.assertEqual(policy, inputs["policy"])
        self.assertEqual(event, {
            "schema_version": "FIRST_DIALOGUE_EVENT_V1",
            "event_id": "p41-validation",
            **{field: inputs["state"][field] for field in decision_api.SCOPE_FIELDS},
            "observed_at": inputs["now"].isoformat(), "kind": "AUTOREPLY",
        })
        self.assertEqual(decision.call_args.kwargs, {"now": inputs["now"]})
        outbound.assert_called_once_with(inputs["action"])
        receipt.assert_called_once_with(inputs["attempt"], inputs["receipt"])

    def test_all_outbound_mail_kinds_allowed_with_current_thread(self):
        for kind in ("SEND", "REPLY", "FOLLOWUP"):
            with self.subTest(kind=kind):
                inputs = fixture()
                inputs["action"]["kind"] = kind
                self.assertEqual(outbound_api.build_outbound_intent(inputs["action"])["state"], "PREPARED")
                self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")

    def test_valid_original_handoff_is_not_a_send_source(self):
        inputs = handoff_fixture()
        self.assertEqual(outbound_api.build_outbound_intent(inputs["action"])["state"], "PREPARED")
        self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_accepted_and_delivered_preserve_receipt_semantics(self):
        for outcome, reconcile in (("ACCEPTED", True), ("DELIVERED", False)):
            with self.subTest(outcome=outcome):
                inputs = fixture()
                inputs["receipt"]["outcome"] = outcome
                before = receipt_api.normalize_send_receipt(inputs["attempt"], inputs["receipt"])
                self.assertEqual(self.go(inputs)["source_state"], outcome)
                self.assertIs(before["reconciliation_required"], reconcile)
                self.assertFalse(before["resend_authorized"])
                self.assertEqual(receipt_api.normalize_send_receipt(inputs["attempt"], inputs["receipt"]), before)

    def test_all_unconfirmed_outcomes_and_null_receipt(self):
        for outcome in ("TIMEOUT", "UNKNOWN", "NOT_SENT", None):
            for phase in ("PREPARED", "DISPATCH_STARTED"):
                with self.subTest(outcome=outcome, phase=phase):
                    inputs = fixture()
                    inputs["attempt"]["phase"] = phase
                    if outcome is None:
                        inputs["receipt"] = None
                    else:
                        inputs["receipt"].update(
                            outcome=outcome, provider_message_id=None,
                            evidence_ref="evidence-test" if outcome == "NOT_SENT" else None,
                            not_sent_reason=(
                                "NOT_DISPATCHED" if phase == "PREPARED"
                                else "PROVIDER_REJECTED_BEFORE_ACCEPTANCE"
                            ) if outcome == "NOT_SENT" else None,
                        )
                    normalized = receipt_api.normalize_send_receipt(inputs["attempt"], inputs["receipt"])
                    self.assertIn(normalized["state"], ("UNKNOWN", "NOT_SENT"))
                    self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_inconsistent_provider_receipt_is_dependency_rejected(self):
        for field, value in (
            ("provider_message_id", None), ("evidence_ref", None),
            ("not_sent_reason", "NOT_DISPATCHED"), ("outcome", "UNRECOGNIZED"),
        ):
            with self.subTest(field=field):
                inputs = fixture()
                inputs["receipt"][field] = value
                self.reject(inputs, "DEPENDENCY_REJECTED")
        inputs = fixture()
        inputs["attempt"]["phase"] = "PREPARED"
        self.reject(inputs, "DEPENDENCY_REJECTED")

    def test_receipt_disagreement_is_dependency_rejected(self):
        for field, value in (
            ("client_id", "foreign"), ("runtime_campaign_id", "foreign"),
            ("lead_id", "foreign"), ("mailbox_ref", "foreign"),
            ("action_id", "foreign"), ("attempt_no", 2),
            ("payload_sha256", "f" * 64),
        ):
            with self.subTest(field=field):
                inputs = fixture()
                inputs["receipt"][field] = value
                self.reject(inputs, "DEPENDENCY_REJECTED")

    def test_matching_foreign_attempt_and_receipt_is_scope_mismatch(self):
        for field, value in (
            ("client_id", "foreign"),
            ("runtime_campaign_id", "22222222-2222-4222-8222-222222222222"),
            ("lead_id", "foreign"), ("mailbox_ref", "foreign"),
            ("action_id", "foreign"), ("payload_sha256", "f" * 64),
        ):
            with self.subTest(field=field):
                inputs = fixture()
                inputs["attempt"][field] = inputs["receipt"][field] = value
                receipt_api.normalize_send_receipt(inputs["attempt"], inputs["receipt"])
                self.reject(inputs, "SCOPE_MISMATCH")

    def test_scope_links_for_every_field(self):
        for field in b806_api.SCOPE_FIELDS:
            with self.subTest(field=field):
                inputs = fixture()
                inputs["scope"][field] = (
                    "22222222-2222-4222-8222-222222222222" if field == "campaign_id" else "foreign"
                )
                self.reject(inputs, "SCOPE_MISMATCH")

    def test_state_and_action_scope_links_for_every_field(self):
        for field in decision_api.SCOPE_FIELDS:
            for record in ("state", "action_scope"):
                with self.subTest(field=field, record=record):
                    inputs = fixture()
                    target = inputs["state"] if record == "state" else inputs["action"]["scope"]
                    target[field] = "22222222-2222-4222-8222-222222222222" if field == "runtime_campaign_id" else "foreign"
                    # Keep F114's recipient correlation valid to isolate scope.
                    if record == "action_scope" and field == "lead_id":
                        inputs["action"]["payload"]["recipient_ref"] = "foreign"
                        rehash_action(inputs)
                    self.reject(inputs, "SCOPE_MISMATCH")

    def test_sender_mismatch_uses_scope_error_before_f103(self):
        inputs = fixture()
        inputs["policy"]["sender_ref"] = "foreign-mailbox"
        self.reject(inputs, "SCOPE_MISMATCH")

    def test_action_policy_locale_and_pack_links(self):
        for field, value in (
            ("action_id", "other-action"), ("policy_version", "other-policy"),
            ("locale", "en-GB"), ("language_pack_version", "other-pack"),
        ):
            with self.subTest(field=field):
                inputs = fixture()
                inputs["action"][field] = value
                self.reject(inputs, "SCOPE_MISMATCH")
        for field in ("policy_version", "source_action_id"):
            inputs = fixture()
            inputs[field] = "different"
            self.reject(inputs, "SCOPE_MISMATCH")
        inputs = fixture()
        inputs["policy"]["policy_version"] = "different"
        self.reject(inputs, "SCOPE_MISMATCH")

    def test_synthetic_locale_works_without_fixed_language_allowlist(self):
        inputs = fixture()
        inputs["policy"].update(locale="de-DE", allowed_locales=["de-DE"], language_pack_versions={"de-DE": "pack-de-test"})
        inputs["action"].update(locale="de-DE", language_pack_version="pack-de-test")
        inputs["policy"]["approved_templates"]["followup-test"]["locale"] = "de-DE"
        self.assertEqual(self.go(inputs)["policy_version"], "policy-test")

    def test_historical_action_revision_need_not_equal_current_state(self):
        inputs = fixture()
        inputs["state"]["revision"] = 1000
        inputs["action"]["expected_state_revision"] = 0
        self.assertEqual(self.go(inputs)["source_action_id"], "action-test")

    def test_fences(self):
        for status in ("STOPPED", "HANDED_OFF"):
            with self.subTest(status=status):
                inputs = fixture()
                inputs["state"]["status"] = status
                self.reject(inputs, "FENCED")
        inputs = fixture()
        inputs["state"]["status"] = "UNRECOGNIZED"
        self.reject(inputs, "DEPENDENCY_REJECTED")

    def test_f103_required_fields_and_schemas_are_not_reimplemented(self):
        for record, field in (
            ("state", "processed_event_ids"), ("policy", "approved_fact_ids"),
            ("policy", "language_pack_versions"), ("policy", "allowed_locales"),
        ):
            with self.subTest(record=record, field=field):
                inputs = fixture()
                del inputs[record][field]
                self.reject(inputs, "DEPENDENCY_REJECTED")
        for record in ("state", "policy", "action", "attempt", "receipt"):
            inputs = fixture()
            inputs[record]["schema_version"] = "UNRECOGNIZED"
            self.reject(inputs, "DEPENDENCY_REJECTED")

    def test_original_payload_validation_and_hash_are_required(self):
        for field, value in (("body", "changed synthetic body"), ("recipient_ref", "foreign"), ("body", "")):
            with self.subTest(field=field, value=value):
                inputs = fixture()
                inputs["action"]["payload"][field] = value
                self.reject(inputs, "DEPENDENCY_REJECTED")
        inputs = fixture()
        inputs["action"]["kind"] = "NOT_A_KIND"
        self.reject(inputs, "DEPENDENCY_REJECTED")

    def test_scope_exact_shape_and_null_thread_rejected(self):
        for value in (None, [], True, {}, {**fixture()["scope"], "extra": "x"}):
            inputs = fixture()
            inputs["scope"] = value
            self.reject(inputs, "INVALID_INPUT")
        for field in b806_api.SCOPE_FIELDS:
            inputs = fixture()
            del inputs["scope"][field]
            self.reject(inputs, "INVALID_INPUT")
        inputs = fixture()
        inputs["scope"]["thread_id"] = None
        self.reject(inputs, "INVALID_INPUT")

    def test_plain_identifier_boundaries(self):
        bad = ("", " x", "x ", "x\n", "x\x00y", "x\x7fy", "x" * 129, "bad\ud800", True, 1, None)
        for target in ("client_id", "thread_id", "policy_version", "source_action_id"):
            for value in bad:
                with self.subTest(target=target, kind=type(value).__name__):
                    inputs = fixture()
                    if target in inputs["scope"]:
                        inputs["scope"][target] = value
                    else:
                        inputs[target] = value
                    self.reject(inputs, "INVALID_INPUT")
        for value in ("x", "я" * 128, "scope:with space inside"):
            inputs = fixture()
            change_scope(inputs, "client_id", value)
            self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")

    def test_uuid_canonical_and_nil(self):
        for value in (
            "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "11111111111141118111111111111111",
            "{11111111-1111-4111-8111-111111111111}", "urn:uuid:11111111-1111-4111-8111-111111111111",
            "not-a-uuid", "１１１１１１１１-1111-4111-8111-111111111111",
        ):
            with self.subTest(value=value):
                inputs = fixture()
                inputs["scope"]["campaign_id"] = value
                self.reject(inputs, "INVALID_INPUT")
        inputs = fixture()
        change_scope(inputs, "campaign_id", "00000000-0000-0000-0000-000000000000")
        self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")

    def test_non_json_types_rejected_before_dependencies(self):
        for value in (1.5, float("nan"), float("inf"), (), set(), b"x", object()):
            inputs = fixture()
            inputs["state"]["additional"] = value
            with (
                patch.object(decision_api, "decide_next_action") as decision,
                patch.object(outbound_api, "build_outbound_intent") as outbound,
                patch.object(receipt_api, "normalize_send_receipt") as receipt,
            ):
                self.reject(inputs, "INVALID_INPUT")
            decision.assert_not_called()
            outbound.assert_not_called()
            receipt.assert_not_called()

    def test_user_hooks_and_container_subclasses_are_not_executed(self):
        calls = []

        def forbidden(*args, **kwargs):
            calls.append("hook")
            raise AssertionError("User hook executed")

        class HookObject:
            __eq__ = __bool__ = __str__ = __repr__ = __deepcopy__ = forbidden

        class HookDict(dict):
            __iter__ = __getitem__ = items = keys = get = __deepcopy__ = forbidden

        class HookList(list):
            __iter__ = __getitem__ = __deepcopy__ = forbidden

        class HookStr(str):
            __str__ = __eq__ = strip = encode = __deepcopy__ = forbidden

        for value in (HookObject(), HookDict(), HookList(), HookStr("x")):
            inputs = fixture()
            inputs["state"]["additional"] = value
            self.reject(inputs, "INVALID_INPUT")
        self.assertEqual(calls, [])

    def test_non_string_json_keys_rejected(self):
        for key in (1, None, ("x",)):
            inputs = fixture()
            inputs["state"]["extra"] = {key: "value"}
            self.reject(inputs, "INVALID_INPUT")

    def test_type_metaclass_comparison_hook_is_not_executed(self):
        calls = []

        class HookMeta(type):
            def __eq__(cls, other):
                calls.append("metaclass equality")
                raise AssertionError("Type equality hook executed")

            def __hash__(cls):
                calls.append("metaclass hash")
                raise AssertionError("Type hash hook executed")

        class HookObject(metaclass=HookMeta):
            pass

        inputs = fixture()
        inputs["state"]["extra"] = HookObject()
        self.reject(inputs, "INVALID_INPUT")
        self.assertEqual(calls, [])

    def test_fake_now_class_property_is_not_executed(self):
        calls = []

        class FakeNow:
            @property
            def __class__(self):
                calls.append("class property")
                raise AssertionError("Fake datetime class hook executed")

        inputs = fixture()
        inputs["now"] = FakeNow()
        self.reject(inputs, "INVALID_INPUT")
        self.assertEqual(calls, [])

    def test_malformed_record_types_never_escape_as_raw_exceptions(self):
        for record in ("state", "policy", "action", "attempt", "receipt"):
            for value in (None, [], True, "record", 42, {}):
                with self.subTest(record=record, kind=type(value).__name__):
                    inputs = fixture()
                    inputs[record] = value
                    self.reject(inputs)

    def test_circular_json_is_rejected_without_recursion_failure(self):
        for kind in ("dict", "list"):
            inputs = fixture()
            if kind == "dict":
                circular = {}
                circular["self"] = circular
            else:
                circular = []
                circular.append(circular)
            inputs["state"]["extra"] = circular
            self.reject(inputs, "INVALID_INPUT")

    def test_valid_aliases_and_extra_f103_fields_are_preserved(self):
        inputs = fixture()
        shared = [None, True, False, 1, "extra", {"nested": []}]
        inputs["state"]["extra_one"] = shared
        inputs["state"]["extra_two"] = shared
        inputs["policy"]["extra"] = shared
        with patch.object(decision_api, "decide_next_action", wraps=decision_api.decide_next_action) as decision:
            self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")
        self.assertEqual(decision.call_args.args[0]["extra_one"], shared)
        self.assertEqual(decision.call_args.args[2]["extra"], shared)
        self.assertIs(inputs["state"]["extra_one"], inputs["state"]["extra_two"])

    def test_deep_valid_extra_fields_have_no_invented_depth_limit(self):
        inputs = fixture()
        nested = None
        for _ in range(2000):
            nested = [nested]
        inputs["state"]["extra"] = nested
        self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")

    def test_timestamp_normalization_preserves_microseconds(self):
        for text, expected in (
            ("2026-10-20T12:00:00.1+02:00", "2026-10-20T10:00:00.100000Z"),
            ("2026-10-20T08:00:00.123456-02:00", "2026-10-20T10:00:00.123456Z"),
            ("2026-10-20T10:00:00.000001+00:00", "2026-10-20T10:00:00.000001Z"),
            ("2026-10-20T10:00:00.000000Z", "2026-10-20T10:00:00Z"),
        ):
            with self.subTest(text=text):
                inputs = fixture()
                inputs["state"]["last_outbound_at"] = text
                self.assertEqual(self.go(inputs)["last_outbound_at"], expected)

    def test_bad_timestamp_forms_are_source_unconfirmed(self):
        for value in (
            None, "", True, 5, "2026-10-20", "2026-10-20T10:00:00",
            "2026-10-20 10:00:00Z", "2026-10-20t10:00:00z",
            "2026-10-20T10:00:00-00:00", "2026-10-20T10:00:00.1234567Z",
            "2026-10-20T10:00:00.Z", "2026-02-30T10:00:00Z",
            "2026-10-20T24:00:00Z", "2026-10-20T10:00:60Z",
            "2026-10-20T10:00:00+24:00", "2026-10-20T10:00:00+00:60",
            "2026-10-20T10:00:00+01:00:01", "２０２６-10-20T10:00:00Z",
            "2026-10-20T10:00:00Z\n", "2026-10-20T10:00:00Z ",
        ):
            with self.subTest(kind=type(value).__name__, value=value):
                inputs = fixture()
                inputs["state"]["last_outbound_at"] = value
                self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_future_timestamp_one_microsecond_and_exact_now(self):
        inputs = fixture()
        inputs["state"]["last_outbound_at"] = inputs["now"].isoformat()
        self.assertEqual(self.go(inputs)["last_outbound_at"], "2026-10-20T12:00:00Z")
        inputs["state"]["last_outbound_at"] = (inputs["now"] + dt.timedelta(microseconds=1)).isoformat()
        self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_timestamp_utc_and_cooldown_overflows_are_source_unconfirmed(self):
        for value in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            inputs = fixture()
            inputs["now"] = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
            inputs["state"]["last_outbound_at"] = value
            inputs["policy"]["cooldown_seconds"] = 0
            self.reject(inputs, "SOURCE_UNCONFIRMED")
        for timestamp, cooldown in (("9999-12-31T23:59:59Z", 1), ("2026-10-20T10:00:00Z", 10 ** 50)):
            inputs = fixture()
            inputs["now"] = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
            inputs["state"]["last_outbound_at"] = timestamp
            inputs["policy"]["cooldown_seconds"] = cooldown
            self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_representable_extreme_times_with_zero_cooldown(self):
        for value in ("0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z"):
            inputs = fixture()
            inputs["now"] = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
            inputs["state"]["last_outbound_at"] = value
            inputs["policy"]["cooldown_seconds"] = 0
            self.assertEqual(self.go(inputs)["last_outbound_at"], value)

    def test_now_requires_real_aware_datetime_and_accepts_subclass(self):
        class DatetimeSubclass(dt.datetime):
            pass

        for value in (None, True, "2026-10-20T12:00:00Z", dt.datetime(2026, 10, 20)):
            inputs = fixture()
            inputs["now"] = value
            self.reject(inputs, "INVALID_INPUT")
        inputs = fixture()
        inputs["now"] = DatetimeSubclass(2026, 10, 20, 12, tzinfo=dt.timezone.utc)
        self.assertEqual(self.go(inputs)["source_state"], "DELIVERED")

    def test_broken_now_timezone_is_contract_error_not_raw_exception(self):
        class BrokenTimezone(dt.tzinfo):
            def utcoffset(self, value):
                raise ValueError("synthetic timezone failure")

        inputs = fixture()
        inputs["now"] = dt.datetime(2026, 10, 20, tzinfo=BrokenTimezone())
        self.reject(inputs, "INVALID_INPUT")

    def test_timestamp_is_only_taken_from_state(self):
        inputs = fixture()
        inputs["state"]["last_outbound_at"] = "2026-10-19T04:05:06.000007Z"
        inputs["state"]["observed_at"] = "2026-10-20T12:00:00Z"
        self.assertEqual(self.go(inputs)["last_outbound_at"], "2026-10-19T04:05:06.000007Z")
        inputs["state"]["last_outbound_at"] = None
        self.reject(inputs, "SOURCE_UNCONFIRMED")

    def test_exact_numeric_types_and_nonnegative_values(self):
        for record, field in (("state", "followups_sent"), ("policy", "max_followups"), ("policy", "cooldown_seconds")):
            for value in (True, False, -1, 1.5, "2", None, []):
                with self.subTest(record=record, field=field, kind=type(value).__name__):
                    inputs = fixture()
                    inputs[record][field] = value
                    self.reject(inputs)

    def test_zero_and_exhausted_caps_remain_valid_basis(self):
        for cap, sent in ((0, 0), (0, 1), (3, 3), (3, 99)):
            with self.subTest(cap=cap, sent=sent):
                inputs = fixture()
                inputs["policy"]["max_followups"] = cap
                inputs["state"]["followups_sent"] = sent
                actual = self.go(inputs)
                self.assertEqual((actual["max_followups"], actual["followups_sent"]), (cap, sent))
                ports = Ports(actual, inputs["scope"])
                self.assertEqual(b806_api.Service(ports).execute("schedule", self.request(inputs))["status"], "INVALID")
                self.assertEqual(ports.rows, {})

    def test_real_consumer_window_boundary_ordinal_and_stable_identity(self):
        inputs = fixture()
        basis = self.go(inputs)
        ports = Ports(basis, inputs["scope"])
        service = b806_api.Service(ports)
        too_early = self.request(inputs, "2026-10-20T10:59:59.999999Z")
        self.assertEqual(service.execute("schedule", too_early)["status"], "INVALID")
        self.assertEqual(ports.rows, {})
        self.assertEqual(service.execute("schedule", self.request(inputs, ordinal=0))["status"], "INVALID")
        request = self.request(inputs)
        actual = service.execute("schedule", request)
        self.assertEqual(actual["status"], "OK")
        identity = [inputs["scope"], inputs["policy_version"], inputs["source_action_id"], 1]
        canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        self.assertEqual(actual["data"]["schedule_id"], hashlib.sha256(canonical).hexdigest())
        replay = service.execute("schedule", request)
        self.assertEqual(replay["status"], "REPLAY")
        self.assertEqual(replay["data"], actual["data"])
        self.assertEqual(len(ports.rows), 1)

    def test_real_consumer_replay_precedes_changed_current_basis(self):
        inputs = fixture()
        ports = Ports(self.go(inputs), inputs["scope"])
        service = b806_api.Service(ports)
        request = self.request(inputs)
        self.assertEqual(service.execute("schedule", request)["status"], "OK")
        first_call_count = len(ports.calls)
        ports.basis = None
        self.assertEqual(service.execute("schedule", request)["status"], "REPLAY")
        self.assertEqual(len(ports.calls), first_call_count)
        changed = self.request(inputs, "2026-10-20T12:00:00Z")
        self.assertEqual(service.execute("schedule", changed)["status"], "CONFLICT")
        self.assertEqual(len(ports.rows), 1)

    def test_zero_cooldown_allows_same_instant(self):
        inputs = fixture()
        inputs["policy"]["cooldown_seconds"] = 0
        basis = self.go(inputs)
        ports = Ports(basis, inputs["scope"])
        result = b806_api.Service(ports).execute("schedule", self.request(inputs, basis["last_outbound_at"]))
        self.assertEqual(result["status"], "OK")

    def test_inputs_unchanged_and_result_fresh_for_repeated_calls(self):
        inputs = fixture()
        before = copy.deepcopy(inputs)
        one, two = self.go(inputs), self.go(inputs)
        self.assertEqual(inputs, before)
        self.assertEqual(one, two)
        self.assertIsNot(one, two)
        one["source_state"] = "changed"
        self.assertEqual(self.go(inputs), two)
        self.assertEqual(inputs["state"]["processed_event_ids"], [])
        self.assertEqual(inputs["state"]["followups_sent"], 1)
        self.assertEqual(inputs["action"]["action_id"], inputs["source_action_id"])

    def test_rejection_does_not_mutate_inputs_or_leak_payload(self):
        inputs = fixture()
        inputs["action"]["payload"]["body"] = "SYNTHETIC_PRIVATE_MARKER"
        before = copy.deepcopy(inputs)
        error = self.reject(inputs, "DEPENDENCY_REJECTED")
        self.assertEqual(inputs, before)
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", str(error))

    def test_import_and_projection_under_io_clock_process_denial(self):
        source = Path(basis_api.__file__).read_text(encoding="utf-8")
        code = compile(source, "candidate_basis_import.py", "exec")
        inputs = fixture()

        def forbidden(*args, **kwargs):
            raise AssertionError("Forbidden I/O, clock, network or process effect")

        with ExitStack() as stack:
            for module, methods in (
                (builtins, ("open",)), (io, ("open",)),
                (os, ("open", "stat", "lstat", "listdir", "scandir", "getcwd", "getenv")),
                (socket, ("socket", "create_connection")),
                (subprocess, ("Popen", "run")),
                (time, ("time", "monotonic", "sleep")),
            ):
                for method in methods:
                    stack.enter_context(patch.object(module, method, side_effect=forbidden))
            for method in ("open", "read_bytes", "read_text", "stat", "lstat", "resolve", "exists", "is_file", "is_dir", "is_symlink", "iterdir", "glob", "rglob", "cwd"):
                stack.enter_context(patch.object(Path, method, side_effect=forbidden))
            namespace = {"__name__": "candidate_basis_import_check"}
            exec(code, namespace)
            actual = namespace["project_schedule_basis"](**inputs)
            self.assertEqual(actual["source_state"], "DELIVERED")
        tree = ast.parse(source)
        system_clock_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"now", "utcnow", "today", "utcfromtimestamp"}
        ]
        self.assertEqual(system_clock_calls, [])


if __name__ == "__main__":
    unittest.main()
