import copy
import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone, timedelta

# Добавляем родительскую папку в path, чтобы импортировать sales_first_decision из tests/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sales_first_decision import (  # noqa: E402
    decide_next_action, FirstDecisionError, Action, DECISION_KEYS,
)

# Папка с фикстурами и общая «точка отсчёта» времени для всех тестов
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "first_decision"
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def load(name):
    """Читает JSON-фикстуру по имени файла."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_event(kind, **overrides):
    """Берёт базовое событие и подменяет kind + любые дополнительные поля."""
    ev = load("event_question.json")
    ev["kind"] = kind
    ev.update(overrides)
    return ev


class FirstDecisionTest(unittest.TestCase):

    # ---------- Схема выходного словаря ----------

    def test_output_schema_for_all_actions(self):
        """Каждое решение должно содержать ровно 12 ключей FIRST_DECISION_V1."""
        # Набор сценариев, покрывающих разные ветки правил
        scenarios = [
            ("HUMAN_QUESTION", load("state_active.json"), load("policy_en.json")),
            ("OPTOUT", load("state_active.json"), load("policy_en.json")),
            ("INTEREST", load("state_active.json"), load("policy_en.json")),
            ("UNKNOWN", load("state_active.json"), load("policy_en.json")),
            ("AUTOREPLY", load("state_active.json"), load("policy_en.json")),
            ("HUMAN_QUESTION", load("state_stopped.json"), load("policy_en.json")),
            ("HUMAN_QUESTION", load("state_active.json"), load("policy_ru.json")),
        ]
        for kind, state, policy in scenarios:
            result = decide_next_action(state, make_event(kind), policy, now=NOW)
            # Все обязательные ключи на месте
            for key in DECISION_KEYS:
                self.assertIn(key, result, f"{kind}: missing key {key}")
            # Версия схемы и действие — из допустимых значений
            self.assertEqual(result["schema_version"], "FIRST_DECISION_V1")
            self.assertIn(result["action"], Action.ALL)

    # ---------- Порядок правил (precedence) ----------

    def test_question_proposes_reply_en(self):
        # Вопрос клиента + валидный EN-шаблон -> предложить ответ
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)
        self.assertEqual(r["template_id"], "explain")
        self.assertEqual(r["fact_ids"], ["fact-test"])
        self.assertEqual(r["locale"], "en-GB")
        self.assertEqual(r["language_pack_version"], "v1")
        self.assertFalse(r["cancel_followups"])

    def test_objection_proposes_reply_en(self):
        # Возражение обрабатывается так же, как вопрос
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_OBJECTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_interest_handoff(self):
        # INTEREST -> передать ответственному и отменить follow-up
        r = decide_next_action(load("state_active.json"),
                               make_event("INTEREST"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.HANDOFF)
        self.assertEqual(r["next_status"], "HANDED_OFF")
        self.assertTrue(r["cancel_followups"])

    def test_optout_stops(self):
        # OPTOUT -> остановить диалог
        r = decide_next_action(load("state_active.json"),
                               make_event("OPTOUT"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.STOP)
        self.assertEqual(r["next_status"], "STOPPED")
        self.assertTrue(r["cancel_followups"])

    def test_bounce_stops(self):
        # BOUNCE (недоставка) -> STOP
        r = decide_next_action(load("state_active.json"),
                               make_event("BOUNCE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.STOP)

    def test_autoreply_noop(self):
        # Автоответ -> ничего не делаем, follow-up не отменяем
        r = decide_next_action(load("state_active.json"),
                               make_event("AUTOREPLY"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertFalse(r["cancel_followups"])

    def test_unknown_quarantine(self):
        # Непонятное событие -> карантин с отменой follow-up
        r = decide_next_action(load("state_active.json"),
                               make_event("UNKNOWN"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)
        self.assertTrue(r["cancel_followups"])

    def test_duplicate_event_noop(self):
        # Событие с уже обработанным event_id -> NOOP
        state = load("state_active.json")
        state["processed_event_ids"] = ["evt-1"]
        r = decide_next_action(state, make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertEqual(r["reason"], "duplicate_event")
        self.assertFalse(r["cancel_followups"])

    def test_already_stopped_noop(self):
        # Диалог уже STOPPED -> NOOP с отменой follow-up
        r = decide_next_action(load("state_stopped.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertTrue(r["cancel_followups"])

    def test_already_handed_off_noop(self):
        # Диалог уже HANDED_OFF -> NOOP с отменой follow-up
        r = decide_next_action(load("state_handed_off.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertTrue(r["cancel_followups"])

    def test_optout_above_handed_off(self):
        # OPTOUT имеет приоритет над «уже закрыто» — всё равно STOP
        r = decide_next_action(load("state_handed_off.json"),
                               make_event("OPTOUT"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.STOP)

    def test_optout_above_stopped(self):
        # OPTOUT при STOPPED тоже даёт STOP
        r = decide_next_action(load("state_stopped.json"),
                               make_event("OPTOUT"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.STOP)

    def test_bounce_above_handed_off(self):
        # BOUNCE при HANDED_OFF тоже даёт STOP
        r = decide_next_action(load("state_handed_off.json"),
                               make_event("BOUNCE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.STOP)

    def test_interest_at_stopped_is_noop(self):
        # INTEREST ниже «уже закрыто» — правило не срабатывает
        r = decide_next_action(load("state_stopped.json"),
                               make_event("INTEREST"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)

    def test_autoreply_at_stopped_is_noop(self):
        # AUTOREPLY при STOPPED — тоже NOOP
        r = decide_next_action(load("state_stopped.json"),
                               make_event("AUTOREPLY"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)

    def test_duplicate_above_stop(self):
        # Дубликат выше STOP — даже OPTOUT-дубликат даёт NOOP
        state = load("state_active.json")
        state["processed_event_ids"] = ["evt-1"]
        r = decide_next_action(state, make_event("OPTOUT"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)

    # ---------- Валидация схемы и scope ----------

    def test_scope_mismatch_raises(self):
        # client_id в event != state -> ошибка scope_mismatch
        ev = make_event("HUMAN_QUESTION")
        ev["client_id"] = "other"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), ev,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "scope_mismatch")

    def test_missing_client_id_both_raises(self):
        # client_id отсутствует и в state, и в event -> ошибка по отсутствующему полю state
        state = load("state_active.json")
        event = make_event("HUMAN_QUESTION")
        del state["client_id"]
        del event["client_id"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, event, load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "missing_state_field")

    def test_corrupted_status_raises(self):
        # Неизвестный статус -> invalid_status (не должен пропускать до бизнес-правил)
        state = load("state_active.json")
        state["status"] = "CORRUPTED"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_status")

    def test_bad_state_schema_raises(self):
        # Неверная версия схемы state
        state = load("state_active.json")
        state["schema_version"] = "WRONG"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "bad_state_schema")

    def test_bad_event_schema_raises(self):
        # Неверная версия схемы event
        event = make_event("HUMAN_QUESTION")
        event["schema_version"] = "WRONG"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "bad_event_schema")

    def test_bad_policy_schema_raises(self):
        # Неверная версия схемы policy
        policy = load("policy_en.json")
        policy["schema_version"] = "WRONG"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)
        self.assertEqual(ctx.exception.code, "bad_policy_schema")

    def test_missing_event_id_raises(self):
        # Отсутствует event_id -> доменная ошибка с кодом missing_event_field
        event = make_event("HUMAN_QUESTION")
        del event["event_id"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "missing_event_field")

    def test_bad_observed_at_format_raises(self):
        # Кривая строка даты -> invalid_observed_at (не ValueError)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "not-a-date"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_observed_at")

    def test_sender_mismatch_raises(self):
        # sender_ref policy != mailbox_ref state
        policy = load("policy_en.json")
        policy["sender_ref"] = "wrong-mailbox"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)
        self.assertEqual(ctx.exception.code, "sender_mismatch")

    def test_absent_sender_ref_raises(self):
        # sender_ref вообще отсутствует -> missing_policy_field
        policy = load("policy_en.json")
        del policy["sender_ref"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)
        self.assertEqual(ctx.exception.code, "missing_policy_field")

    def test_empty_product_id_raises(self):
        # Пустой product_id -> empty_field
        policy = load("policy_en.json")
        policy["product_id"] = ""
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)
        self.assertEqual(ctx.exception.code, "empty_field")

    def test_missing_locale_raises(self):
        # Пустая локаль -> ошибка
        policy = load("policy_en.json")
        policy["locale"] = ""
        with self.assertRaises(FirstDecisionError):
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)

    def test_int_field_bool_raises(self):
        # bool вместо int -> invalid_int (True не должен проходить как 1)
        state = load("state_active.json")
        state["revision"] = True
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_int")

    def test_negative_followups_sent_raises(self):
        # Отрицательный followups_sent -> invalid_int
        state = load("state_active.json")
        state["followups_sent"] = -1
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_int")

    def test_string_max_followups_raises(self):
        # Строка вместо int -> invalid_int
        policy = load("policy_en.json")
        policy["max_followups"] = "2"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_int")

    def test_naive_now_raises(self):
        # now без таймзоны -> naive_now
        naive = datetime(2026, 10, 11, 12, 0, 0)
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=naive)
        self.assertEqual(ctx.exception.code, "naive_now")

    def test_naive_observed_at_raises(self):
        # observed_at без таймзоны -> ошибка
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:00:00"
        with self.assertRaises(FirstDecisionError):
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)

    def test_observed_at_in_future_raises(self):
        # observed_at более чем на 300 сек в будущем -> observed_in_future
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T15:00:00+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "observed_in_future")

    def test_unknown_kind_raises(self):
        # Неизвестный kind -> unknown_kind на этапе валидации
        event = make_event("SOMETHING_ELSE")
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "unknown_kind")

    def test_naive_last_outbound_raises(self):
        # last_outbound_at без таймзоны -> ошибка
        state = load("state_active.json")
        state["last_outbound_at"] = "2026-10-10T12:00:00"
        with self.assertRaises(FirstDecisionError):
            decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)

    # ---------- Шаблоны и факты ----------

    def test_template_unknown_id_quarantines(self):
        # Ссылка на несуществующий шаблон -> карантин
        event = make_event("HUMAN_QUESTION", approved_template_id="nope")
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)

    def test_template_missing_locale_quarantines(self):
        # template.locale != policy.locale -> карантин, а не ошибка схемы
        policy = load("policy_en.json")
        policy["approved_templates"]["explain"]["locale"] = "ru-RU"
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)
        self.assertTrue(r["cancel_followups"])

    def test_template_fact_outside_approved_quarantines(self):
        # fact вне approved_fact_ids -> карантин
        policy = load("policy_en.json")
        policy["approved_templates"]["explain"]["fact_ids"] = ["other-fact"]
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)

    # ---------- FOLLOWUP_DUE ----------

    def test_followup_due_no_last_outbound_noop(self):
        # Нет last_outbound_at -> NOOP, follow-up не отменяется
        r = decide_next_action(load("state_active.json"),
                               make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertEqual(r["reason"], "no_last_outbound")

    def test_followup_due_cap_reached_noop(self):
        # Достигнут лимит follow-up -> NOOP
        state = load("state_active.json")
        state["followups_sent"] = 2
        state["last_outbound_at"] = "2026-10-09T12:00:00+00:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertEqual(r["reason"], "followup_cap_reached")

    def test_followup_due_cooldown_not_passed_noop(self):
        # Прошло меньше cooldown -> NOOP
        state = load("state_active.json")
        state["followups_sent"] = 1
        state["last_outbound_at"] = "2026-10-11T11:00:00+00:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertEqual(r["reason"], "cooldown_not_passed")

    def test_followup_due_exactly_cooldown_proposes(self):
        # Ровно cooldown прошёл -> можно предложить follow-up
        state = load("state_active.json")
        state["followups_sent"] = 1
        state["last_outbound_at"] = "2026-10-10T12:00:00+00:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_FOLLOWUP)

    def test_followup_due_last_outbound_in_future_raises(self):
        # last_outbound_at в будущем -> ошибка
        state = load("state_active.json")
        state["last_outbound_at"] = "2026-10-11T13:00:00+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(ctx.exception.code, "last_outbound_in_future")

    def test_followup_due_cap_zero_blocks(self):
        # max_followups=0 блокирует любые follow-up
        policy = load("policy_en.json")
        policy["max_followups"] = 0
        state = load("state_active.json")
        state["last_outbound_at"] = "2026-10-09T12:00:00+00:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"), policy, now=NOW)
        self.assertEqual(r["action"], Action.NOOP)
        self.assertEqual(r["reason"], "followup_cap_reached")

    def test_followup_preserves_locale_and_pack_version(self):
        # Follow-up сохраняет locale и версию языкового пакета
        state = load("state_active.json")
        state["followups_sent"] = 1
        state["last_outbound_at"] = "2026-10-10T12:00:00+00:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["locale"], "en-GB")
        self.assertEqual(r["language_pack_version"], "v1")

    def test_observed_at_near_max_datetime_with_offset_within_tolerance(self):
        # QA-кейс: now=9999-12-31T23:59:59.999999+03:00, observed=9999-12-31T21:01:00+00:00
        # Разница 60.000001с < 300с — ожидается PROPOSE_REPLY, не ошибка
        max_now = datetime(9999, 12, 31, 23, 59, 59, 999999,
                           tzinfo=timezone(timedelta(hours=3)))
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "9999-12-31T21:01:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=max_now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_exactly_300_seconds_boundary_utc(self):
        # observed = now + ровно 300с — граница включена
        now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:05:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_one_microsecond_over_300s(self):
        # observed = now + 300с + 1мкс — уже в будущем
        now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:05:00.000001+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=now)
        self.assertEqual(ctx.exception.code, "observed_in_future")

    def test_observed_at_300s_boundary_with_positive_offset(self):
        # now с +05:00, observed в UTC — граница 300с должна соблюдаться
        now = datetime(2026, 10, 11, 17, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:05:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_300s_boundary_with_negative_offset(self):
        # now с -07:00, observed в UTC — граница 300с должна соблюдаться
        now = datetime(2026, 10, 11, 5, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:05:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_when_now_is_datetime_min_with_offset(self):
        # QA-кейс: now = минимальный datetime с offset +01:00.
        # observed_at далеко в будущем — должна быть ошибка observed_in_future,
        # а не пропуск проверки из-за OverflowError.
        min_now = datetime(1, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=1)))
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T12:00:00+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=min_now)
        self.assertEqual(ctx.exception.code, "observed_in_future")

    def test_observed_at_at_datetime_min_boundary(self):
        # now = 0001-01-01 00:00:00 UTC, observed = тот же момент — валидно
        min_now = datetime(1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "0001-01-01T00:00:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=min_now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_min_datetime_exactly_300s(self):
        # now = 0001-01-01 00:00:00 UTC, observed = +300с — валидно (граница включена)
        min_now = datetime(1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "0001-01-01T00:05:00+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=min_now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_min_datetime_one_microsecond_over_300s(self):
        # now = 0001-01-01 00:00:00 UTC, observed = +300с + 1мкс — ошибка
        min_now = datetime(1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "0001-01-01T00:05:00.000001+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=min_now)
        self.assertEqual(ctx.exception.code, "observed_in_future")

    def test_observed_at_max_datetime_boundary(self):
        # now = 9999-12-31 23:59:59 UTC, observed = тот же момент — валидно
        max_now = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "9999-12-31T23:59:59+00:00"
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=max_now)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_observed_at_max_datetime_one_microsecond_over_300s(self):
        # now = 9999-12-31 23:54:59 UTC, observed = +300с+1мкс — ошибка
        max_now = datetime(9999, 12, 31, 23, 54, 59, tzinfo=timezone.utc)
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "9999-12-31T23:59:59.000001+00:00"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=max_now)
        self.assertEqual(ctx.exception.code, "observed_in_future")

    # ---------- Часовые пояса (UTC offsets) ----------

    def test_observed_at_with_positive_offset_accepted(self):
        # observed_at с +03:00 корректно приводится к UTC
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "2026-10-11T15:00:00+03:00"  # = 12:00 UTC
        r = decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)

    def test_last_outbound_with_negative_offset_accepted(self):
        # last_outbound с -05:00 тоже валиден (это ровно cooldown назад по UTC)
        state = load("state_active.json")
        state["followups_sent"] = 1
        state["last_outbound_at"] = "2026-10-10T07:00:00-05:00"
        r = decide_next_action(state, make_event("FOLLOWUP_DUE"),
                               load("policy_en.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_FOLLOWUP)

    # ---------- Языки (R4) ----------

    def test_ru_locale_template_proposes(self):
        # RU-политика с RU-шаблоном -> предложить ответ
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_ru.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)
        self.assertEqual(r["locale"], "ru-RU")
        self.assertEqual(r["language_pack_version"], "v1")

    def test_ru_dialogue_with_en_template_quarantines(self):
        # RU-политика, но шаблон EN -> карантин
        policy = load("policy_ru.json")
        policy["approved_templates"]["explain"]["locale"] = "en-GB"
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)

    def test_synthetic_de_locale_works_without_code_change(self):
        # R4: третий язык добавляется данными, ядро не трогаем
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_de_synthetic.json"), now=NOW)
        self.assertEqual(r["action"], Action.PROPOSE_REPLY)
        self.assertEqual(r["locale"], "de-DE")
        self.assertEqual(r["language_pack_version"], "v1")

    def test_locale_not_in_allowed_raises(self):
        # locale не входит в allowed_locales -> ошибка
        policy = load("policy_en.json")
        policy["locale"] = "fr-FR"
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "locale_not_allowed")

    def test_missing_pack_version_raises(self):
        # Для активной локали нет версии пакета -> ошибка
        policy = load("policy_en.json")
        del policy["language_pack_versions"]["en-GB"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "missing_pack_version")

    def test_empty_pack_version_raises(self):
        # Пустая строка версии -> invalid_pack_version
        policy = load("policy_en.json")
        policy["language_pack_versions"]["en-GB"] = ""
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_pack_version")

    def test_whitespace_only_pack_version_raises(self):
        # Строка из пробелов -> invalid_pack_version
        policy = load("policy_en.json")
        policy["language_pack_versions"]["en-GB"] = "   "
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_pack_version")

    def test_number_pack_version_raises(self):
        # Число вместо строки -> invalid_pack_version
        policy = load("policy_en.json")
        policy["language_pack_versions"]["en-GB"] = 1
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_pack_version")

    def test_bool_pack_version_raises(self):
        # bool вместо строки -> invalid_pack_version
        policy = load("policy_en.json")
        policy["language_pack_versions"]["en-GB"] = True
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_pack_version")

    def test_empty_language_pack_versions_raises(self):
        # Пустой словарь версий -> empty_language_pack_versions
        policy = load("policy_en.json")
        policy["language_pack_versions"] = {}
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "empty_language_pack_versions")

    def test_duplicate_locales_raises(self):
        # Дубли в allowed_locales -> duplicate_locales
        policy = load("policy_en.json")
        policy["allowed_locales"] = ["en-GB", "en-GB", "ru-RU"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "duplicate_locales")

    def test_whitespace_around_locale_raises(self):
        # Пробелы вокруг активной локали -> locale_has_whitespace
        policy = load("policy_en.json")
        policy["locale"] = " en-GB "
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "locale_has_whitespace")

    def test_whitespace_around_allowed_locale_raises(self):
        # Пробелы вокруг элемента allowed_locales -> locale_has_whitespace
        policy = load("policy_en.json")
        policy["allowed_locales"] = [" en-GB", "ru-RU"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "locale_has_whitespace")

    def test_nonstring_allowed_locale_raises(self):
        # Не-строка в allowed_locales -> invalid_locale_id
        policy = load("policy_en.json")
        policy["allowed_locales"] = ["en-GB", 5]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_locale_id")

    # ---------- Проверки элементов fact_ids ----------

    def test_number_in_approved_fact_ids_raises(self):
        # Число в approved_fact_ids -> invalid_approved_fact_id (не пропустится)
        policy = load("policy_en.json")
        policy["approved_fact_ids"] = [1, 2, 3]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_approved_fact_id")

    def test_dict_in_approved_fact_ids_raises(self):
        # {} в approved_fact_ids -> invalid_approved_fact_id, а не TypeError
        policy = load("policy_en.json")
        policy["approved_fact_ids"] = [{}]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_approved_fact_id")

    def test_mixed_types_in_approved_fact_ids_raises(self):
        # Смесь строки и числа -> invalid_approved_fact_id
        policy = load("policy_en.json")
        policy["approved_fact_ids"] = ["fact-test", 42]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_approved_fact_id")

    def test_empty_string_in_approved_fact_ids_raises(self):
        # Пустая строка в approved_fact_ids -> invalid_approved_fact_id
        policy = load("policy_en.json")
        policy["approved_fact_ids"] = ["fact-test", ""]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_approved_fact_id")

    def test_duplicate_approved_fact_ids_raises(self):
        # Дубли в approved_fact_ids -> duplicate_approved_fact_id
        policy = load("policy_en.json")
        policy["approved_fact_ids"] = ["fact-test", "fact-test"]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "duplicate_approved_fact_id")

    def test_number_in_template_fact_ids_raises(self):
        # Число в template.fact_ids -> bad_template_fact_id
        policy = load("policy_en.json")
        policy["approved_templates"]["explain"]["fact_ids"] = [1]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "bad_template_fact_id")

    def test_dict_in_template_fact_ids_raises(self):
        # {} в template.fact_ids -> bad_template_fact_id, а не TypeError
        policy = load("policy_en.json")
        policy["approved_templates"]["explain"]["fact_ids"] = [{}]
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(ctx.exception.code, "bad_template_fact_id")

    def test_valid_string_fact_not_in_approved_still_quarantines(self):
        # Корректный строковый fact_id, но не входит в approved -> QUARANTINE (не ошибка)
        policy = load("policy_en.json")
        policy["approved_templates"]["explain"]["fact_ids"] = ["other-fact"]
        r = decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"), policy, now=NOW)
        self.assertEqual(r["action"], Action.QUARANTINE)

    def test_now_with_tzinfo_but_no_offset_raises(self):
        # tzinfo есть, но utcoffset() -> None: должно ловиться FirstDecisionError
        from datetime import tzinfo as _tzinfo

        class FakeNoOffset(_tzinfo):
            def utcoffset(self, dt):
                return None
            def dst(self, dt):
                return None
            def tzname(self, dt):
                return "FAKE"

        bad_now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=FakeNoOffset())
        with self.assertRaises(FirstDecisionError) as ctx:
            decide_next_action(load("state_active.json"),
                               make_event("HUMAN_QUESTION"),
                               load("policy_en.json"), now=bad_now)
        self.assertEqual(ctx.exception.code, "invalid_now_offset")

    # ---------- Инварианты ----------

    def test_no_mutation_of_inputs(self):
        # Функция не должна мутировать входные словари
        state = load("state_active.json")
        event = make_event("HUMAN_QUESTION")
        policy = load("policy_en.json")
        sb = copy.deepcopy(state)
        eb = copy.deepcopy(event)
        pb = copy.deepcopy(policy)
        decide_next_action(state, event, policy, now=NOW)
        self.assertEqual(state, sb)
        self.assertEqual(event, eb)
        self.assertEqual(policy, pb)

    def test_deterministic_replay(self):
        # Один и тот же вход дважды -> идентичный выход
        state = load("state_active.json")
        event = make_event("HUMAN_QUESTION")
        policy = load("policy_en.json")
        r1 = decide_next_action(state, event, policy, now=NOW)
        r2 = decide_next_action(state, event, policy, now=NOW)
        self.assertEqual(r1, r2)

    def test_max_datetime_now_does_not_overflow(self):
        # now на максимуме: проверка границы 300 сек не должна падать с OverflowError
        max_now = datetime.max.replace(tzinfo=timezone.utc)
        # observed_at на максимуме — это не будущее и не прошлое, граничный случай
        event = make_event("HUMAN_QUESTION")
        event["observed_at"] = "9999-12-31T23:59:59+00:00"
        # главное — не падает с OverflowError; результат либо решение, либо FirstDecisionError
        try:
            decide_next_action(load("state_active.json"), event,
                               load("policy_en.json"), now=max_now)
        except FirstDecisionError:
            pass  # это допустимо, главное — не OverflowError


if __name__ == "__main__":
    unittest.main()