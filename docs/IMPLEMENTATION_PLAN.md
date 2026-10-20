# План реализации SFA-P41-BASIS R1

Статус: план подготовлен; исходная заготовка не реализована. Нормативные источники — исходные TASK_RU.md, CONTRACT.md, TESTING.md, INTEGRATION.md, RETURN.md и ENGINEERING.json в `03_Dmitriy/`.

## Этапы и контрольные точки

| Этап | Действие | Результат и проверка |
|---|---|---|
| 1. Исходная база | Сохранить исходный ZIP, структуру и hashes. Проверить доступный Python 3.12.x и происхождение импортов | CRC ZIP; совпадение всех 38 файлов; 35 protected hashes; реальные логи исходных проверок |
| 2. Входные данные | В единственном разрешённом basis.py реализовать точный ABI, ScheduleBasisError, безопасный обход builtin JSON domain, форму scope, ID/UUID и aware now | Отказ на malformed типах без вызова пользовательских hooks и без неконтролируемых исключений; допустимые дополнительные поля F103 сохранены |
| 3. Корреляции и время | Проверить связи клиента/кампании/диалога, sender, action/policy/locale/pack; строгий last_outbound_at и представимость cooldown | SCOPE_MISMATCH для чужих данных; SOURCE_UNCONFIRMED для недопустимого времени/переполнения. Эти проверки размещены так, чтобы F103 не подменил требуемые коды |
| 4. Исходные API | Вызвать неизменные публичные F103 decide_next_action, F114 build_outbound_intent, F108 normalize_send_receipt | Реальные вызовы; F103 получает внутреннее несохраняемое AUTOREPLY p41-validation с original state scope и now.isoformat(); их выходы не сохраняются |
| 5. DTO | Вернуть новый exact dict семи полей из проверенных источников | ACCEPTED не повышается до DELIVERED; входы не мутируются; равные входы дают равный результат; нет эффектов |
| 6. Собственные тесты | Добавить только tests/test_candidate_*.py; использовать исходные API и публичный B806.Service.execute | Положительные сценарии, отрицательные границы, OK/REPLAY, неверное время schedule, cap и отсутствие новых источников истины |
| 7. Первый контроль | Пройти все обязательные gates, проверить diff и protected hashes | 138 исходных + 19 public + все собственные тесты, без skips/expectedFailures/нулевых прогонов; реальные logs/exit codes |
| 8. Возврат и второй контроль | Собрать требуемый ZIP, распаковать в чистую папку, проверить manifest и повторить gates | Проверены реальные байты финального ZIP и происхождение импортов; один актуальный архив; статусы не преувеличены |

## Существенные сценарии собственных тестов

- SEND/REPLY/FOLLOWUP, корректный HANDOFF с ожидаемым SOURCE_UNCONFIRMED; STOPPED/HANDED_OFF → FENCED.
- Все F108 outcomes, обе фазы attempt и receipt=None там, где допускает F108. ACCEPTED сохраняет исходный смысл; недоставленные/неопределённые результаты не дают basis.
- Различать disagreement attempt↔receipt (DEPENDENCY_REJECTED) и согласованную чужую пару относительно action (SCOPE_MISMATCH).
- Полная идентичность scope с явным campaign_id → runtime_campaign_id, source_action_id, payload hash, policy version, locale и language pack.
- Допустимое отличие текущей state.revision от исторической action.expected_state_revision. SEND с ненулевым thread допустим.
- Exact builtin JSON domain без container subclasses/object hooks, циклы и допустимые общие ссылки; plain ID 1..128, UTF-8, C0/DEL, canonical lowercase UUID.
- Строгий ASCII RFC3339, даты/смещения, null, future, -00:00, дроби до шести знаков, UTC normalization с микросекундами, предельные даты и переполнение timedelta.
- Exact неотрицательные int без bool; zero cooldown, нулевой и исчерпанный cap допустимы в DTO. Решение о допустимости schedule остаётся B8-06.
- Входы не изменяются, возвращается независимый dict; нет I/O, системных часов, глобального изменяемого состояния и утечки входных данных в ошибках.
- Отдельно подтвердить точный вызов F103 с синтетическим AUTOREPLY и отсутствие сохранения validation decision/action.

Не добавлять произвольные новые ограничения, например exact-type для now, если их нет в контракте. Реальные причины ошибок окружения отделять от дефекта компонента.

## Возврат

`SFA-P41-BASIS_R1_RETURN.zip` → единственный корень `SFA-P41-BASIS_R1_RETURN/`, внутри непосредственно содержимое исходного `03_Dmitriy/` с разрешёнными изменениями. Корневые README/CURSOR_PROMPT, исходный ZIP и preparation-материалы репозитория в сдачу не включаются.

Обязательные `return/`: CHANGELOG.md, MANIFEST_SHA256.json, TRACEABILITY.json, COMMANDS.json, LIMITATIONS.md, CONNECT_AND_ROLLBACK.md и сырые logs/. Manifest охватывает все файлы возврата кроме себя. COMMANDS фиксирует interpreter/cwd/argv/exit/stdout/stderr/log hashes/import origins. Требование связывается с кодом, конкретным тестом и фактическим результатом.

COMPONENT_IMPLEMENTED/NOT_IMPLEMENTED и PASS/FAIL/NOT_RUN указываются отдельно. Успех синтетического B806 consumer не доказывает PostgreSQL, аутентифицированный production, атомарность или полный родительский P41. Исправление исходного контракта выполняется через координатора до реализации, без изменения protected files.

После двух успешных контролей сохранить решение, материалы возврата и единственный актуальный ZIP в этом репозитории без перезаписи чужих изменений. Приложить commit и SHA-256 архива. При блокере дать точную причину и не заявлять готовность.
