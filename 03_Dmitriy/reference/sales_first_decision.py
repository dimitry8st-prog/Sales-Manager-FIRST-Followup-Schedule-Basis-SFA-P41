from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


# ---------- Константы ----------
class Action:
    NOOP = "NOOP"
    STOP = "STOP"
    HANDOFF = "HANDOFF"
    PROPOSE_REPLY = "PROPOSE_REPLY"
    PROPOSE_FOLLOWUP = "PROPOSE_FOLLOWUP"
    QUARANTINE = "QUARANTINE"
    # Список всех допустимых действий — используется в тестах и валидации выхода
    ALL = (NOOP, STOP, HANDOFF, PROPOSE_REPLY, PROPOSE_FOLLOWUP, QUARANTINE)


class Status:
    ACTIVE = "ACTIVE"
    HANDED_OFF = "HANDED_OFF"
    STOPPED = "STOPPED"
    # Все допустимые статусы диалога
    ALL = (ACTIVE, HANDED_OFF, STOPPED)


class Kind:
    HUMAN_QUESTION = "HUMAN_QUESTION"
    HUMAN_OBJECTION = "HUMAN_OBJECTION"
    INTEREST = "INTEREST"
    OPTOUT = "OPTOUT"
    BOUNCE = "BOUNCE"
    AUTOREPLY = "AUTOREPLY"
    UNKNOWN = "UNKNOWN"
    FOLLOWUP_DUE = "FOLLOWUP_DUE"
    # Все допустимые виды событий
    ALL = (HUMAN_QUESTION, HUMAN_OBJECTION, INTEREST, OPTOUT, BOUNCE,
           AUTOREPLY, UNKNOWN, FOLLOWUP_DUE)


# Точные версии схем из CONTRACT.md
SCHEMA_DECISION = "FIRST_DECISION_V1"
SCHEMA_STATE = "FIRST_DIALOGUE_STATE_V1"
SCHEMA_EVENT = "FIRST_DIALOGUE_EVENT_V1"
SCHEMA_POLICY = "FIRST_POLICY_V1"

# Пять полей scope, которые должны совпадать в state и event
SCOPE_FIELDS = ("client_id", "runtime_campaign_id", "lead_id", "mailbox_ref", "thread_id")

# Полный набор ключей выходного словаря FIRST_DECISION_V1
DECISION_KEYS = (
    "schema_version", "action", "reason", "expected_state_revision",
    "event_id", "policy_version", "template_id", "fact_ids",
    "next_status", "cancel_followups", "locale", "language_pack_version",
)

# Обязательные поля входных структур
STATE_REQUIRED = SCOPE_FIELDS + (
    "schema_version", "status", "revision", "processed_event_ids",
    "last_outbound_at", "followups_sent",
)

EVENT_REQUIRED = SCOPE_FIELDS + (
    "schema_version", "event_id", "kind", "observed_at",
)

POLICY_REQUIRED = (
    "schema_version", "policy_version", "product_id", "locale",
    "sender_ref", "max_followups", "cooldown_seconds",
    "approved_fact_ids", "approved_templates",
    "allowed_locales", "language_pack_versions",
)


# ---------- Исключение ----------
class FirstDecisionError(ValueError):
    """Кидается при невалидном входе. .code — стабильная короткая метка причины."""
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# ---------- Вспомогательные функции ----------
def _parse_aware(value: Any, code: str) -> datetime:
    """Парсит ISO-дату, требует timezone-aware. При любой проблеме кидает FirstDecisionError."""
    # Должна быть непустая строка
    if not isinstance(value, str) or not value.strip():
        raise FirstDecisionError(code, "not a non-empty string")
    # Пробуем распарсить; любые ошибки конвертируем в своё исключение
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise FirstDecisionError(code, f"bad datetime: {exc}") from exc
    # Наивные даты (без зоны) нам не подходят
    if dt.tzinfo is None:
        raise FirstDecisionError(code, "naive datetime")
    return dt


# Константы для перевода datetime в целые микросекунды от начала эпохи UTC.
# Используем int, чтобы не зависеть от границ типа datetime
# и не ловить OverflowError на краевых датах.
_EPOCH_ORDINAL = datetime(1, 1, 1).toordinal()  # = 1
_MICROS_PER_SEC = 1_000_000
_MICROS_PER_DAY = 86_400 * _MICROS_PER_SEC


def _to_utc_micros(dt: datetime) -> int:
    """
    Переводит aware-datetime в целое число микросекунд от 0001-01-01 00:00:00 UTC.
    Работает корректно для всех дат диапазона datetime, включая границы.
    Не использует astimezone, чтобы избежать OverflowError при переводе краевых дат.
    """
    offset = dt.utcoffset()
    if offset is None:
        raise FirstDecisionError("invalid_offset", "utcoffset() is None")

    days_since_epoch = dt.toordinal() - _EPOCH_ORDINAL
    micros_in_day = (
        dt.hour * 3_600 * _MICROS_PER_SEC
        + dt.minute * 60 * _MICROS_PER_SEC
        + dt.second * _MICROS_PER_SEC
        + dt.microsecond
    )
    naive_micros = days_since_epoch * _MICROS_PER_DAY + micros_in_day

    # offset считаем целыми микросекундами без float (через days/seconds/microseconds)
    offset_micros = (
        offset.days * _MICROS_PER_DAY
        + offset.seconds * _MICROS_PER_SEC
        + offset.microseconds
    )
    return naive_micros - offset_micros


def _require_keys(obj: Any, keys, code: str) -> None:
    """Проверяет, что объект — dict и содержит все обязательные ключи."""
    if not isinstance(obj, dict):
        raise FirstDecisionError(code, "not a dict")
    for k in keys:
        if k not in obj:
            raise FirstDecisionError(code, f"missing key: {k}")


# ---------- Валидация входа ----------
def _validate(state: Dict[str, Any], event: Dict[str, Any],
              policy: Dict[str, Any], now: datetime) -> None:
    """Все проверки входа выполняются ДО бизнес-правил. При провале — FirstDecisionError."""

    # now должен быть datetime с реальным UTC-смещением
    if not isinstance(now, datetime):
        raise FirstDecisionError("invalid_now", "now must be datetime")
    if now.tzinfo is None:
        raise FirstDecisionError("naive_now", "now must be timezone-aware")
    if now.utcoffset() is None:
        raise FirstDecisionError("invalid_now_offset", "now has no UTC offset")

    # Обязательные ключи присутствуют во всех трёх словарях
    _require_keys(state, STATE_REQUIRED, "missing_state_field")
    _require_keys(event, EVENT_REQUIRED, "missing_event_field")
    _require_keys(policy, POLICY_REQUIRED, "missing_policy_field")

    # Точные версии схем
    if state["schema_version"] != SCHEMA_STATE:
        raise FirstDecisionError("bad_state_schema", str(state["schema_version"]))
    if event["schema_version"] != SCHEMA_EVENT:
        raise FirstDecisionError("bad_event_schema", str(event["schema_version"]))
    if policy["schema_version"] != SCHEMA_POLICY:
        raise FirstDecisionError("bad_policy_schema", str(policy["schema_version"]))

    # scope event должен совпадать со scope state по всем 5 полям
    for field in SCOPE_FIELDS:
        if event[field] != state[field]:
            raise FirstDecisionError("scope_mismatch", f"event.{field} != state.{field}")

    # Статус должен быть одним из допустимых
    if state["status"] not in Status.ALL:
        raise FirstDecisionError("invalid_status", str(state["status"]))

    # Вид события должен быть одним из допустимых
    if event["kind"] not in Kind.ALL:
        raise FirstDecisionError("unknown_kind", str(event["kind"]))

    # Отправитель в policy должен совпадать с ящиком в state
    if policy["sender_ref"] != state["mailbox_ref"]:
        raise FirstDecisionError("sender_mismatch")

    # Целочисленные поля: настоящий int (не bool) и >= 0
    for name, value in (
        ("revision", state["revision"]),
        ("followups_sent", state["followups_sent"]),
        ("max_followups", policy["max_followups"]),
        ("cooldown_seconds", policy["cooldown_seconds"]),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FirstDecisionError("invalid_int", name)

    # processed_event_ids должен быть списком
    if not isinstance(state["processed_event_ids"], list):
        raise FirstDecisionError("invalid_processed_event_ids")

    # Непустые строковые поля policy
    for name in ("product_id", "locale", "policy_version"):
        v = policy[name]
        if not isinstance(v, str) or not v.strip():
            raise FirstDecisionError("empty_field", name)

    # locale без пробелов по краям
    locale = policy["locale"]
    if locale != locale.strip():
        raise FirstDecisionError("locale_has_whitespace", locale)

    # allowed_locales: непустой список непустых строк без пробелов и без дублей
    allowed = policy["allowed_locales"]
    if not isinstance(allowed, list) or not allowed:
        raise FirstDecisionError("empty_allowed_locales")
    seen = set()
    for item in allowed:
        if not isinstance(item, str) or not item.strip():
            raise FirstDecisionError("invalid_locale_id", repr(item))
        if item != item.strip():
            raise FirstDecisionError("locale_has_whitespace", item)
        if item in seen:
            raise FirstDecisionError("duplicate_locales", item)
        seen.add(item)

    # Активная локаль должна входить в список разрешённых
    if locale not in allowed:
        raise FirstDecisionError("locale_not_allowed", locale)

    # language_pack_versions: dict[непустая строка -> непустая строка]
    versions = policy["language_pack_versions"]
    if not isinstance(versions, dict) or not versions:
        raise FirstDecisionError("empty_language_pack_versions")
    for key, value in versions.items():
        if not isinstance(key, str) or not key.strip():
            raise FirstDecisionError("invalid_pack_locale", repr(key))
        if key != key.strip():
            raise FirstDecisionError("locale_has_whitespace", key)
        if not isinstance(value, str) or not value.strip():
            raise FirstDecisionError("invalid_pack_version", repr(value))
        if value != value.strip():
            raise FirstDecisionError("invalid_pack_version", "whitespace")

    # Для активной локали должна быть непустая версия пакета
    if locale not in versions:
        raise FirstDecisionError("missing_pack_version", locale)

    # Даты: aware и не в будущем (допуск 300 секунд включительно).
    # Сравнение — в целочисленных микросекундах от начала эпохи UTC.
    # Это исключает OverflowError на краевых датах и не сужает допуск.
    observed = _parse_aware(event["observed_at"], "invalid_observed_at")

    now_micros = _to_utc_micros(now)
    observed_micros = _to_utc_micros(observed)
    tolerance_micros = 300 * _MICROS_PER_SEC

    if observed_micros > now_micros + tolerance_micros:
        raise FirstDecisionError("observed_in_future")

    # Проверка last_outbound_at: он не может быть в будущем
    if state["last_outbound_at"] is not None:
        last = _parse_aware(state["last_outbound_at"], "invalid_last_outbound_at")
        if _to_utc_micros(last) > now_micros:
            raise FirstDecisionError("last_outbound_in_future")

    # Шаблоны
    templates = policy["approved_templates"]
    if not isinstance(templates, dict):
        raise FirstDecisionError("invalid_templates")
    approved_facts = policy["approved_fact_ids"]
    if not isinstance(approved_facts, list):
        raise FirstDecisionError("invalid_approved_facts")
    approved_seen = set()
    for fact in approved_facts:
        if not isinstance(fact, str) or not fact.strip():
            raise FirstDecisionError("invalid_approved_fact_id", repr(fact))
        if fact != fact.strip():
            raise FirstDecisionError("invalid_approved_fact_id", "whitespace")
        if fact in approved_seen:
            raise FirstDecisionError("duplicate_approved_fact_id", fact)
        approved_seen.add(fact)

    for tpl_id, tpl in templates.items():
        if not isinstance(tpl_id, str) or not tpl_id.strip():
            raise FirstDecisionError("invalid_template_id", repr(tpl_id))
        if not isinstance(tpl, dict):
            raise FirstDecisionError("bad_template", tpl_id)
        body = tpl.get("body")
        if not isinstance(body, str) or not body.strip():
            raise FirstDecisionError("empty_template_body", tpl_id)
        facts = tpl.get("fact_ids")
        if not isinstance(facts, list):
            raise FirstDecisionError("bad_template_facts", tpl_id)
        for fact in facts:
            if not isinstance(fact, str) or not fact.strip():
                raise FirstDecisionError("bad_template_fact_id", tpl_id)
        tpl_locale = tpl.get("locale")
        if not isinstance(tpl_locale, str) or not tpl_locale.strip():
            raise FirstDecisionError("missing_template_locale", tpl_id)


# ---------- Сборка ответа ----------
def _make(action: str, reason: str, state: Dict[str, Any],
          event: Dict[str, Any], policy: Dict[str, Any],
          next_status: str, cancel: bool,
          template_id: Optional[str] = None,
          fact_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Собирает полный ответ FIRST_DECISION_V1 со всеми 12 ключами."""
    locale = policy["locale"]
    return {
        "schema_version": SCHEMA_DECISION,
        "action": action,
        "reason": reason,
        "expected_state_revision": state["revision"],
        "event_id": event["event_id"],
        "policy_version": policy["policy_version"],
        "template_id": template_id,
        "fact_ids": list(fact_ids) if fact_ids else [],
        "next_status": next_status,
        "cancel_followups": bool(cancel),
        "locale": locale,
        "language_pack_version": policy["language_pack_versions"][locale],
    }


# ---------- Выбор шаблона ----------
def _pick_template(event: Dict[str, Any], policy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Возвращает подходящий шаблон или None. Условия: существует, locale совпадает с policy, fact_ids ⊆ approved."""
    tpl_id = event.get("approved_template_id")
    if not tpl_id or not isinstance(tpl_id, str):
        return None
    tpl = policy["approved_templates"].get(tpl_id)
    if not isinstance(tpl, dict):
        return None
    if tpl.get("locale") != policy["locale"]:
        return None
    approved = set(policy["approved_fact_ids"])
    if not set(tpl.get("fact_ids") or []).issubset(approved):
        return None
    return tpl


# ---------- ГЛАВНАЯ ФУНКЦИЯ ----------
def decide_next_action(state: Dict[str, Any],
                       event: Dict[str, Any],
                       policy: Dict[str, Any],
                       *,
                       now: datetime) -> Dict[str, Any]:
    """Возвращает FIRST_DECISION_V1. Правила применяются строго сверху вниз, первое совпадение выигрывает."""

    # Сначала валидация всего входа — если что-то не так, дальше не идём
    _validate(state, event, policy, now)

    kind = event["kind"]
    status = state["status"]
    tpl = _pick_template(event, policy)

    # Правило 2: дубликат — событие уже обрабатывали
    if event["event_id"] in state["processed_event_ids"]:
        cancel = status in (Status.STOPPED, Status.HANDED_OFF)
        return _make(Action.NOOP, "duplicate_event", state, event, policy,
                     next_status=status, cancel=cancel)

    # Правило 3: OPTOUT / BOUNCE -> STOP (отмена подписки или недоставка)
    if kind in (Kind.OPTOUT, Kind.BOUNCE):
        return _make(Action.STOP, "optout_or_bounce", state, event, policy,
                     next_status=Status.STOPPED, cancel=True)

    # Правило 4: диалог уже закрыт (STOPPED или HANDED_OFF) -> NOOP
    if status in (Status.STOPPED, Status.HANDED_OFF):
        return _make(Action.NOOP, "already_closed", state, event, policy,
                     next_status=status, cancel=True)

    # Правило 5: INTEREST -> передать ответственному (HANDOFF)
    if kind == Kind.INTEREST:
        return _make(Action.HANDOFF, "interest_confirmed", state, event, policy,
                     next_status=Status.HANDED_OFF, cancel=True)

    # Правило 6: UNKNOWN -> карантин (на разбор человеком)
    if kind == Kind.UNKNOWN:
        return _make(Action.QUARANTINE, "unknown_event", state, event, policy,
                     next_status=status, cancel=True)

    # Правило 7: AUTOREPLY -> ничего не делаем
    if kind == Kind.AUTOREPLY:
        return _make(Action.NOOP, "autoreply", state, event, policy,
                     next_status=status, cancel=False)

    # Правило 8: вопрос или возражение -> предложить ответ по шаблону либо карантин
    if kind in (Kind.HUMAN_QUESTION, Kind.HUMAN_OBJECTION):
        if tpl is None:
            return _make(Action.QUARANTINE, "no_valid_template", state, event, policy,
                         next_status=status, cancel=True)
        return _make(Action.PROPOSE_REPLY, "template_ok", state, event, policy,
                     next_status=status, cancel=False,
                     template_id=event.get("approved_template_id"),
                     fact_ids=list(tpl["fact_ids"]))

    # Правило 9: FOLLOWUP_DUE — проверить лимит, cooldown и наличие шаблона
    if kind == Kind.FOLLOWUP_DUE:
        last_raw = state["last_outbound_at"]

        # 9.1 Нет последнего исходящего -> нечего отсчитывать
        if last_raw is None:
            return _make(Action.NOOP, "no_last_outbound", state, event, policy,
                         next_status=status, cancel=False)

        last = _parse_aware(last_raw, "invalid_last_outbound_at")

        # 9.2 Достигнут лимит follow-up
        if state["followups_sent"] >= policy["max_followups"]:
            return _make(Action.NOOP, "followup_cap_reached", state, event, policy,
                         next_status=status, cancel=False)

        # 9.3 Cooldown ещё не прошёл
        elapsed_micros = _to_utc_micros(now) - _to_utc_micros(last)
        cooldown_micros = policy["cooldown_seconds"] * _MICROS_PER_SEC
        if elapsed_micros < cooldown_micros:
            return _make(Action.NOOP, "cooldown_not_passed", state, event, policy,
                         next_status=status, cancel=False)

        # 9.4 Нет валидного шаблона для follow-up -> карантин
        if tpl is None:
            return _make(Action.QUARANTINE, "no_valid_followup_template",
                         state, event, policy, next_status=status, cancel=True)

        # 9.5 Всё ок -> предложить follow-up
        return _make(Action.PROPOSE_FOLLOWUP, "followup_due", state, event, policy,
                     next_status=status, cancel=False,
                     template_id=event.get("approved_template_id"),
                     fact_ids=list(tpl["fact_ids"]))

    # Теоретически сюда попасть нельзя (kind проверен в _validate), но защищаемся
    raise FirstDecisionError("unknown_kind", kind)


