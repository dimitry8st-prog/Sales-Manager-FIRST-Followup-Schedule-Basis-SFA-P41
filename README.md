# SFA-P41-BASIS R1 — основание для планирования follow-up

Учебно-практический компонент проекта `sales-manager-first`: чистый мост от F103 state/policy, проверенного F114 action и нормализованной F108 квитанции к существующему порту B8-06 `schedule.basis`.

**Текущий статус: COMPONENT_IMPLEMENTED; компонентные проверки PASS; интеграция NOT_CONNECTED.** Реализован чистый мост к принятому `schedule.basis`. Внешняя приёмка заказчиком остаётся PENDING.

## Навигация

- [ТЗ](03_Dmitriy/TASK_RU.md)
- [Зафиксированный контракт](03_Dmitriy/CONTRACT.md)
- [Порядок проверок](03_Dmitriy/TESTING.md)
- [Подключение и ограничения](03_Dmitriy/INTEGRATION.md)
- [Состав возврата](03_Dmitriy/RETURN.md)
- [План реализации](docs/IMPLEMENTATION_PLAN.md)
- [Промпт для Cursor](CURSOR_PROMPT.md)
- [Готовый архив возврата](artifacts/SFA-P41-BASIS_R1_RETURN.zip)
- [Фактический результат компонента](03_Dmitriy/return/RESULT.json)
- [Карта «требование → код → тест → сырой лог»](03_Dmitriy/return/TRACEABILITY.json)
- [Команды и сырые логи основного прогона](03_Dmitriy/return/COMMANDS.json)
- [Протокол повторной проверки окончательного ZIP](artifacts/SFA-P41-BASIS_R1_RETURN.validation.json)
- [Оригинальный ZIP](assignment/03_Dmitriy__SFA-P41.zip)
- [Фактические стартовые проверки](preparation/INITIAL_CHECKS.json)

## Исходная база

Оригинальный архив: `03_Dmitriy__SFA-P41.zip`, 57 159 байт, 38 файлов.

SHA-256: `830b55644cac2fe1544248d42462a47e2f78c488d9cfba1eed23bccf1222d3de`.

Выданный пакет находится в `03_Dmitriy/`. Из 38 оригинальных файлов изменён только разрешённый `solution/first_followup_integration/basis.py`; остальные 37 сохранены побайтно. Все 35 защищённых файлов из `PROTECTED_SHA256.json` совпадают с оригиналом. Добавлены только собственный `tests/test_candidate_basis.py` и материалы `return/`. Файлы сопровождения репозитория находятся снаружи выданного пакета.

## Как работает компонент

Вызов `project_schedule_basis(...)` выполняет следующие шаги:

1. Проверяет допустимые встроенные JSON-типы без пользовательских hooks, форму scope, ID/UUID и timezone-aware `now`.
2. Связывает state с scope и проверяет timestamp из `state.last_outbound_at` по строгому RFC3339. Несогласованные scope/sender отклоняются.
3. Передаёт полные state/policy исходной публичной F103 `decide_next_action` с внутренним несохраняемым `AUTOREPLY`. STOPPED/HANDED_OFF блокируют basis.
4. Вызывает исходную F114 `build_outbound_intent` для полной проверки исходного действия и его payload/hash, затем сверяет action/policy/locale/language pack. HANDOFF не может стать подтверждением исходящей отправки.
5. Вызывает исходную F108 `normalize_send_receipt`, связывает нормализованную квитанцию с action/scope/payload. Только ACCEPTED или DELIVERED дают basis; ACCEPTED сохраняет исходный смысл.
6. Проверяет представимость cooldown и возвращает новый словарь ровно из семи полей.

| Поле результата | Источник |
|---|---|
| `policy_version` | Проверенное совпадение аргумента, action и policy |
| `source_action_id` | Аргумент, связанный с action и нормализованной квитанцией |
| `source_state` | Нормализованный результат F108, ACCEPTED или DELIVERED |
| `last_outbound_at` | Только state; нормализация в UTC с сохранением микросекунд |
| `max_followups` | Policy, exact int >= 0 |
| `cooldown_seconds` | Policy, exact int >= 0 |
| `followups_sent` | State, exact int >= 0 |

B8-06 получает DTO и сам проверяет ordinal/cap/cooldown при создании schedule. Нулевой/исчерпанный cap остаётся допустимым исходным значением DTO. Ревизия текущего состояния не обязана совпадать с исторической ревизией action.

Ошибка имеет тип `ScheduleBasisError(ValueError)`, `.code` и `str(error)==code`: INVALID_INPUT, DEPENDENCY_REJECTED, SCOPE_MISMATCH, SOURCE_UNCONFIRMED или FENCED. Сообщение не включает входные данные. Входы не изменяются; повторный вызов с равными входами даёт равный результат и новый dict.

Компонент не создаёт расписания, не отправляет сообщения, не изменяет состояние и не назначает доверенность входам. Сопоставление timestamp с конкретной подтверждённой отправкой обязан гарантировать центральный trusted owner: у F108 собственного timestamp нет.

## Запуск проверок реализации

Требуется Python 3.12.x, только стандартная библиотека. Из корня репозитория:

```bash
cd 03_Dmitriy
python -B acceptance/run_reference.py
python -B acceptance/check_package.py
python -B acceptance/run_public.py
python -B RUN_CANDIDATE_TESTS.py
```

В Windows можно использовать `py -3.12 -B` вместо `python -B`.

Основной прогон на Python 3.12.14 / Linux: **138 исходных + 19 публичных + 47 собственных = 204 тестовых метода**, без skips/expectedFailure. Собственные тесты выполняют реальные F103/F114/F108 и публичный B8-06 на синтетических портах: окно/ordinal/cap, OK/REPLAY/CONFLICT и сохранение schedule identity. Также проверены типы/hooks/циклы/общие ссылки, UTF-8, границы дат/переполнения, язык/версии, отсутствие мутаций и эффектов. Subtests не выдаются за отдельные тестовые методы.

**Повторный независимый прогон из окончательного ZIP: те же 204 теста PASS**, все четыре обязательных команды с exit 0; skips/expectedFailure — 0. Проверены CRC, точный состав manifest, все 35 protected hashes, происхождение импортов, допустимый diff, ссылки карты требований и отсутствие изменения распакованных файлов после тестов. SHA-256 ZIP после повторной проверки сохранился.

Исходный `check_package.py` содержит фиксированное сообщение `implementation: NOT_IMPLEMENTED` и сохранён без изменений. Его `preparation: PASS` проверяет базу, а готовность реализации подтверждают остальные gates и отчёт возврата.

Исторические результаты неизменённой заготовки находятся в `preparation/`: baseline ожидал `BASELINE_EXPECTED_MISSING_IMPLEMENTATION`, обычная public-проверка ожидаемо завершалась с ошибками заготовки. Baseline не используется как gate готовой реализации.

## Возврат

`artifacts/SFA-P41-BASIS_R1_RETURN.zip` имеет один корень `SFA-P41-BASIS_R1_RETURN/` и содержит пакет непосредственно внутри. В нём 58 файлов, manifest содержит 57 записей (все файлы кроме самого manifest). Включены обязательные документы, фактические команды/логи, карта требований и собственные тесты. Оригинальный ZIP и root-документы репозитория в сдачу не входят.

SHA-256 готового ZIP: `c89c940410e1164b526842ea82bb62d0c9e5658093ba28c1e10746d1bacff296`.

Протокол повторной проверки распакованного ZIP хранится рядом с архивом и привязан к его SHA-256. Это сохраняет проверенные байты архива неизменными и не создаёт самоссылку в manifest. Внешние сырые логи находятся в `artifacts/verification/round2/`.

## Разрешённые изменения и границы

Внутри `03_Dmitriy/` разрешено менять только `solution/first_followup_integration/basis.py`, добавлять `tests/test_candidate_*.py` и `return/`. Содержимое `reference/`, `acceptance/`, `fixtures/`, исходные manifests и оригинальные компоненты сохраняются неизменными.

Запрещены I/O, SQL, системные часы, новые счётчики, журналы, реальные отправки, schedules и runtime wiring. Полный P41, trusted owner mapping, provenance времени, receipt-once, P05/P19/P15 и connected/production проверки остаются отдельными границами интегратора.

Результат подтверждён для чистого компонентного этапа. PostgreSQL, установленный runtime, authenticated production, due-loop, receipt-once и полный parent P41 этим пакетом не проверены и не объявляются выполненными. Предложение подключения и отката изложено в [CONNECT_AND_ROLLBACK.md](03_Dmitriy/return/CONNECT_AND_ROLLBACK.md); установка/SQL/активация не выполнялись.
