# Проверка

Python 3.12.x, только стандартная библиотека. Запускать из корня пакета. Reference imports берутся только из supplied reference, implementation из solution. Никаких pip/PG/provider для этой границы.

0. `python -B acceptance/run_reference.py` — неизменные исходные F103/F108 regression tests; ожидается exit0.
1. `python -B acceptance/check_package.py` — CRC вне этого дерева проверяет координатор; внутри проверяет protected hashes, обязательный набор, imports/API fixtures и consumer schema. Ожидается exit0.
2. `python -B acceptance/run_public.py --baseline` — на неизменном starter ожидается exit0 и BASELINE_EXPECTED_MISSING_IMPLEMENTATION. Это проверка подготовленного задания, НЕ PASS реализации.
3. После реализации `python -B acceptance/run_public.py` — все public cases должны пройти, exit0. На starter ожидается exit1 с MISSING_IMPLEMENTATION. Вывод не приравнивать к полной parent integration.
4. Собственные meaningful tests: precedence/fences, malformed JSON types, UUID/UTF8 boundaries, locale/version mismatches, all receipt outcomes, timestamp limits, zero/exhausted cap, no mutation, deterministic replay. Команды/exit/stdout/stderr и import origins сохранить в return.

Обязательные проверки нельзя удалить/ослабить; skip/NOT_RUN не PASS. Подмена reference/fixtures/acceptance или создание готовых логов вместо выполнения не принимается. Ошибки среды отделять от дефекта реализации.

Обязательный отдельный gate собственных тестов после реализации: `python -B RUN_CANDIDATE_TESTS.py`. Он требует хотя бы один реально выполненный test_candidate_* и отвергает skip/expectedFailure/нулевой прогон. Публичные обязательные сценарии также не допускают skips/expectedFailures. Все собственные регрессии должны быть исполнены; замена этого gate прямым unittest exit0 не допускается.
