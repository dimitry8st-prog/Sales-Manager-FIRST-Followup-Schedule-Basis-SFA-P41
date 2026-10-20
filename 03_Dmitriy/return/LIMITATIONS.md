# Границы подтверждённого результата

- COMPONENT_IMPLEMENTED / компонентные gates PASS подтверждены только выполненными исходными, публичными и собственными тестами стандартной библиотеки на указанном в COMMANDS.json Python 3.12.x и платформе.
- Реальный публичный B806.Service.execute проверен на синтетических in-memory ports. Это DTO compatibility, не PostgreSQL, не аутентифицированный production и не доказательство атомарности.
- Интеграция: NOT_CONNECTED; установка, SQL, активация, due-loop, реальные отправки и deployment: NOT_RUN. Другие ОС/интерпретаторы вне фактических команд не заявляются проверенными.
- Центральный trusted owner обязан установить authenticated principal/scope/current version, неизменяемую связь action/attempt/receipt и provenance state.last_outbound_at к подтверждённой исходной отправке. В F108 нет timestamp: компонент не может доказать эту связь собственным hash.
- P41/P15/P05/P19, actual accepted owner mappings/SOURCE identity, reply/STOP/HUMAN invalidation, scoped shared locks/transactions, receipt-once persistence и connected tests остаются открытыми задачами соответствующих владельцев.
- ACCEPTED не означает DELIVERED; исходный reconciliation_required не изменяется. Функция ничего не отправляет и не разрешает повторную отправку.
- Нулевой/исчерпанный cap возвращается как проверенное исходное значение; решение о schedule принимает существующий B8-06, включая replay-before-current-basis.
- При нескольких дефектах входа приоритет кодов контрактом не закреплён. Ошибки компонента не означают запись или отправку.
- Итоговый ZIP проверяется после заморозки байтов в отдельной чистой папке. Внешний протокол рядом с ZIP привязан к его SHA-256; добавление такого протокола внутрь уже проверенного ZIP изменило бы проверяемые байты.
- Приёмка заказчиком / production accepted: не заявлена, PENDING / NOT_RUN соответственно.
