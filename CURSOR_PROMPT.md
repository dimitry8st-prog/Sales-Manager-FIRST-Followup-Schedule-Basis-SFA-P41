# Промпт для Cursor — SFA-P41-BASIS R1

Выполни компонентное задание в этом репозитории:
https://github.com/dimitry8st-prog/Sales-Manager-FIRST-Followup-Schedule-Basis-SFA-P41

Рабочая папка задания — `03_Dmitriy/`. Прочитай TASK_RU.md, CONTRACT.md, TESTING.md, INTEGRATION.md, RETURN.md, ENGINEERING.json и исходные компоненты в reference/. Изучи также docs/IMPLEMENTATION_PLAN.md. Нормативный API и ограничения задаёт исходный контракт, а не сопровождающий план.

Реализуй `project_schedule_basis` и `ScheduleBasisError` в `solution/first_followup_integration/basis.py`. Внутри выданного пакета разрешено менять только этот файл, добавлять `tests/test_candidate_*.py` и `return/`. Защищённые файлы сохраняются побайтно. Не переноси ограничения из прошлых заданий автоматически.

Используй реальные неизменные публичные функции:

- F103 `decide_next_action` для проверки полного state/policy через внутреннее несохраняемое AUTOREPLY-событие `p41-validation`, с original scope из state и `now.isoformat()`; validation decision не сохранять и не превращать в предложение.
- F114 `build_outbound_intent` для полной проверки исходного action/payload/hash; подготовленный объект не сохранять.
- F108 `normalize_send_receipt` для attempt/receipt; не писать собственный mapping outcome→state.

До зависимостей проверь допустимый builtin JSON domain без выполнения пользовательских object hooks. Учитывай циклы и допустимые общие ссылки. Не удаляй разрешённые дополнительные поля F103. Проверь exact scope, plain ID, canonical lowercase UUID, timezone-aware now и все предусмотренные контрактом корреляции. `now` не обязан быть exact datetime: не вводи отсутствующее в этом контракте ограничение.

Результат — новый независимый dict ровно из семи полей:
`policy_version, source_action_id, source_state, last_outbound_at, max_followups, cooldown_seconds, followups_sent`.

Обязательные условия:

1. STOPPED/HANDED_OFF → FENCED. Только normalized ACCEPTED/DELIVERED дают basis. ACCEPTED не объявлять DELIVERED и не менять reconciliation_required.
2. `last_outbound_at` только из state. Строгий ASCII RFC3339, дробь до шести знаков, запрет -00:00/future, UTC с сохранением микросекунд. Null/malformed/future и overflow cooldown → SOURCE_UNCONFIRMED. Предусмотри UTC under/overflow. Размести проверки до F103, чтобы сохранить требуемый код ошибки.
3. Несовпадения sender_ref/mailbox_ref и предусмотренных идентичностей → SCOPE_MISMATCH. Учитывай порядок относительно F103.
4. Ошибки F103/F114/F108 → DEPENDENCY_REJECTED. Внутренне согласованная чужая attempt/receipt относительно action → SCOPE_MISMATCH. Возвращаемые ошибки имеют только допустимые `.code` и `str(error)==code`, без входных данных.
5. `state.revision` может отличаться от исторической `action.expected_state_revision`. SEND с ненулевым thread разрешён исходным F114 и подходит мосту. Корректный HANDOFF не даёт basis.
6. Exact int>=0, без bool/defaults. Нулевой/исчерпанный cap допустим в DTO; B8-06 сам проверяет ordinal/cap/cooldown. Не менять schedule identity или replay-before-current-basis.
7. Никаких мутаций, I/O, SQL, системных часов, новых счётчиков, журналов, отправок, schedules или runtime wiring. Структурная проверка не доказывает trusted provenance времени/source action.

Добавь meaningful собственные тесты из плана, включая реальные исходные API и публичный `B8-06.Service.execute` на синтетических портах: OK, повторный REPLAY и отказы границ. Не ограничивайся mock-only тестами. Докажи отсутствие мутаций и запрещённых эффектов. При нескольких дефектах не навязывай неустановленный контрактом приоритет; для одиночных дефектов соблюдай предусмотренный код.

Python 3.12.x, стандартная библиотека. До реализации выполни исходные проверки, включая `acceptance/run_public.py --baseline`. После реализации обязательны из `03_Dmitriy/`:

```bash
python -B acceptance/run_reference.py
python -B acceptance/check_package.py
python -B acceptance/run_public.py
python -B RUN_CANDIDATE_TESTS.py
```

Baseline после реализации не является gate. Исходный check_package содержит фиксированную строку implementation NOT_IMPLEMENTED — не редактируй protected script ради косметического статуса; оценивай готовность по совокупности gates и отчёту. Сохраняй реальные interpreter/cwd/argv/exit/stdout/stderr/log hashes/import origins. Нулевой прогон, skip и expectedFailure не PASS. Если собственной папки tests ещё нет, добавь только разрешённые test_candidate_*.py и проверь именно обязательный runner, не подменяя его другим способом запуска.

Собери один `SFA-P41-BASIS_R1_RETURN.zip` с единственным корнем `SFA-P41-BASIS_R1_RETURN/` и исходной структурой пакета непосредственно внутри. В return/ обязательны CHANGELOG.md, MANIFEST_SHA256.json всех возвращённых файлов кроме самого manifest, TRACEABILITY.json, COMMANDS.json, LIMITATIONS.md, CONNECT_AND_ROLLBACK.md, сырые logs/. Не включай корневые служебные файлы репозитория или исходный архив в возврат. Не публикуй секреты и реальные customer данные.

Перепроверь результат дважды:

- Первый контроль: контракт, все обязательные/собственные тесты, protected hashes, разрешённый diff и traceability.
- Второй контроль: распаковать окончательный ZIP в чистую папку, проверить manifest и protected hashes, повторить все обязательные gates и проверить import origins. Логи повторной проверки можно хранить рядом с ZIP с привязкой к его SHA-256; не меняй уже проверенный ZIP без повторной проверки новых байтов.

При несовместимости контракта остановись и опиши блокер; не меняй защищённую базу. P41/P15/P05/P19, trusted owners, атомарный receipt-once, connected/production integration остаются открытыми границами. Компонентный успех не означает внешнюю приёмку заказчиком.

После двух успешных контролей сохрани решение и единственный актуальный ZIP на GitHub в отдельной рабочей ветке, без force push и без перезаписи чужих изменений. Укажи ветку/commit, SHA-256 ZIP, фактические тесты, ограничения и COMPONENT_IMPLEMENTED. При провале любого обязательного gate не объявляй задачу готовой.
