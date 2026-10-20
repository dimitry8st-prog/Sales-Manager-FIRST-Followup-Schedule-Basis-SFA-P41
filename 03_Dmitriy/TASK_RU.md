# Дмитрий — SFA-P41-BASIS R1

Проект: sales-manager-first. Подготовленный компонентный этап родительского SFA-P41; не повтор B8-06 и не его доработка.

Реализовать чистый мост текущих F103 state/policy + проверенного исходящего action/F108 receipt → точный ответ существующего порта B8-06 `schedule.basis`. Код находится в `solution/first_followup_integration/basis.py`. Вызов и полная семантика зафиксированы в CONTRACT.md.

В supplied source B8-06 `Service._schedule` уже запрашивает именно этот порт, требует ровно семь полей и сам проверяет ordinal, cap и cooldown. F103 уже содержит last_outbound_at/followups_sent и неизменную языковую политику; F108 уже нормализует исходящие квитанции. Их не переписывать. Нужно связать scope, action identity, payload, policy и язык, нормализовать подтверждённое время и вернуть готовый DTO без новых источников истины.

Все данные примера синтетические. Доверенность state/policy/time/receipt устанавливается центральным серверным адаптером вне этого компонента; успешная структурная проверка не делает публичный JSON доверенным.

Разрешено менять только `solution/first_followup_integration/basis.py`, добавлять `tests/test_candidate_*.py` и файлы `return/`. Запрещено менять reference, acceptance, fixtures, manifests и исходные F103/F108/F114/B8-06. Никаких SQL, файлового/сетевого I/O, часов системы, журналов, новых счётчиков, отправок, schedules или runtime wiring. Не переносить source_action_id на новый action, не увеличивать followups_sent и не сбрасывать план по AUTOREPLY.

Результат: реализованный DTO bridge с собственными тестами, публичной приёмкой, трассировкой и предложением подключения. Родительские P41/P15/P05/P19 остаются открыты: actual trusted owner mapping, reply basis/invalidation, locks/receipt-once persistence, due-loop и connected tests не закрываются этим компонентом.
