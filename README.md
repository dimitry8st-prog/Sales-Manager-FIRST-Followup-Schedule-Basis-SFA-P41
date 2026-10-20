# SFA-P41-BASIS R1 — основание для планирования follow-up

Учебно-практический компонент проекта `sales-manager-first`: чистый мост от F103 state/policy, проверенного F114 action и нормализованной F108 квитанции к существующему порту B8-06 `schedule.basis`.

**Текущий статус: исходное задание сохранено; реализация NOT_IMPLEMENTED.** Успешные проверки подготовки не являются приёмкой реализации или подключением к production.

## Навигация

- [ТЗ](03_Dmitriy/TASK_RU.md)
- [Зафиксированный контракт](03_Dmitriy/CONTRACT.md)
- [Порядок проверок](03_Dmitriy/TESTING.md)
- [Подключение и ограничения](03_Dmitriy/INTEGRATION.md)
- [Состав возврата](03_Dmitriy/RETURN.md)
- [План реализации](docs/IMPLEMENTATION_PLAN.md)
- [Промпт для Cursor](CURSOR_PROMPT.md)
- [Оригинальный ZIP](assignment/03_Dmitriy__SFA-P41.zip)
- [Фактические стартовые проверки](preparation/INITIAL_CHECKS.json)

## Исходная база

Оригинальный архив: `03_Dmitriy__SFA-P41.zip`, 57 159 байт, 38 файлов.

SHA-256: `830b55644cac2fe1544248d42462a47e2f78c488d9cfba1eed23bccf1222d3de`.

Выданный пакет находится в `03_Dmitriy/` и сохранён побайтно. В его `PROTECTED_SHA256.json` зафиксировано 35 защищённых файлов. Файлы сопровождения репозитория находятся снаружи выданного пакета.

## Результат будущего компонента

`project_schedule_basis(...)` должен возвращать ровно семь полей:

`policy_version`, `source_action_id`, `source_state`, `last_outbound_at`, `max_followups`, `cooldown_seconds`, `followups_sent`.

Компонент проверяет и сопоставляет данные. Он не создаёт расписания, не отправляет сообщения, не изменяет состояние и не назначает доверенность входам.

## Подготовка и проверки

Требуется Python 3.12.x, только стандартная библиотека. Из корня репозитория:

```bash
cd 03_Dmitriy
python -B acceptance/run_reference.py
python -B acceptance/check_package.py
python -B acceptance/run_public.py --baseline
```

На неизменённой заготовке: 138 исходных тестов проходят; проверка пакета сообщает `preparation: PASS` и `implementation: NOT_IMPLEMENTED`; baseline сообщает `BASELINE_EXPECTED_MISSING_IMPLEMENTATION`.

После реализации вместо baseline обязательны:

```bash
python -B acceptance/run_reference.py
python -B acceptance/check_package.py
python -B acceptance/run_public.py
python -B RUN_CANDIDATE_TESTS.py
```

В публичной приёмке 19 тестовых методов. На заготовке обычный запуск ожидаемо завершается с ошибками `MISSING_IMPLEMENTATION`. Каталог собственных тестов пока отсутствует; его добавляет реализация. Отсутствующие тесты не считаются успешным прогоном.

Исходный `check_package.py` содержит фиксированное сообщение `implementation: NOT_IMPLEMENTED`; после реализации его нельзя редактировать ради смены сообщения. Реальный статус определяется совокупностью проверок и отчётом возврата.

## Разрешённые изменения и границы

Внутри `03_Dmitriy/` разрешено менять только `solution/first_followup_integration/basis.py`, добавлять `tests/test_candidate_*.py` и `return/`. Содержимое `reference/`, `acceptance/`, `fixtures/`, исходные manifests и оригинальные компоненты сохраняются неизменными.

Запрещены I/O, SQL, системные часы, новые счётчики, журналы, реальные отправки, schedules и runtime wiring. Полный P41, trusted owner mapping, provenance времени, receipt-once, P05/P19/P15 и connected/production проверки остаются отдельными границами интегратора.

Окончательная сдача: один `SFA-P41-BASIS_R1_RETURN.zip` с единственной корневой папкой `SFA-P41-BASIS_R1_RETURN/`, как требует [RETURN.md](03_Dmitriy/RETURN.md). Выполнение компонента и независимая приёмка заказчиком фиксируются отдельно.
