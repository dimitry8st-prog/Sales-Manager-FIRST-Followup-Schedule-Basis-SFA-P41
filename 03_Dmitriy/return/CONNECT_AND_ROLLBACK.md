# Предложение подключения и отката — не выполненная установка

## Предварительные условия интегратора

1. Получить точные принятые P05/P19 owner mappings, актуальную SOURCE identity и P41/P15 acceptance vectors. Сверить SHA-256 исходной базы и неизменность accepted B8-06.
2. Сохранить резервную копию точной целевой базы файлов и проверить восстановление. Зафиксировать чужие последующие изменения; не заменять их этим пакетом.
3. В принятом transaction-scoped порте центральный владелец читает текущие F103 state/policy и соответствующие immutable action/attempt/receipt из их владельцев, проверяет authenticated principal/scope/current version и timestamp provenance. Caller/model/email JSON не получает доверенность от структурной валидации.
4. Наложить только собственный basis.py в проверенную integration namespace. Выполнить исходные и новые компонентные тесты с проверкой import origins; отдельно пройти connected gates до установки/активации.

## Предлагаемый вызов

```python
basis = project_schedule_basis(
    scope=trusted_scope,
    policy_version=trusted_policy_version,
    source_action_id=trusted_source_action_id,
    state=trusted_state,
    policy=trusted_policy,
    action=trusted_action,
    attempt=trusted_attempt,
    receipt=trusted_receipt,
    now=trusted_operation_time,
)
```

Вернуть этот exact DTO через существующий `schedule.basis`. `ScheduleBasisError` центральный адаптер сопоставляет с отсутствием допустимого basis / DEPENDENCY_UNAVAILABLE по существующему B806 port contract; schedule/dispatch при этом не создаются. Центральный owner сохраняет shared lock/transaction protocol, replay-before-current-basis, current evidence, STOP/HUMAN/reply invalidation и receipt-once. Existing dialogue.followup codec/due-loop остаются P15.

## Откат интегратора

Откат затрагивает только собственный добавленный модуль/файлы по manifest. Если модуль был установлен впервые, удалить только его собственную копию; если заменялось ранее согласованное содержимое, восстановить именно сохранённую до установки версию после проверки отсутствия последующих чужих изменений. Не откатывать чужие последующие изменения, SQL, данные, журналы, счётчики или accepted runtime. Затем повторить затронутые исходные tests/import checks. Этот документ не является разрешением на установку, SQL или активацию.

## Воспроизводимость из корня возврата

Требуется Python 3.12.x, стандартная библиотека:

```bash
python -B acceptance/run_reference.py
python -B acceptance/check_package.py
python -B acceptance/run_public.py
python -B RUN_CANDIDATE_TESTS.py
```

Baseline применяется только к исходной неизменённой заготовке, а не к готовой реализации. Сначала проверьте MANIFEST_SHA256.json и PROTECTED_SHA256.json. Не запускайте код из неподтверждённых путей импорта.
