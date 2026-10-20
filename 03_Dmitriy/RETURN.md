# Состав возврата

Один ZIP `SFA-P41-BASIS_R1_RETURN.zip`, одна корневая папка `SFA-P41-BASIS_R1_RETURN/`. Сохранить исходную структуру пакета; заменить только разрешённый solution файл, добавить собственные tests и return.

В return: CHANGELOG.md; MANIFEST_SHA256.json всех возвращённых файлов кроме самого manifest; TRACEABILITY.json requirement→file→case→actual result; COMMANDS.json с interpreter/cwd/argv/exit/stdout/stderr/log hashes/import origins; LIMITATIONS.md; CONNECT_AND_ROLLBACK.md. Логи реальных запусков в return/logs/. Ни паролей, owner tokens, контактных адресов, сырого customer body или credentials.

Отдельно указать COMPONENT_IMPLEMENTED/NOT_IMPLEMENTED и PASS/FAIL/NOT_RUN по каждой проверке. Не писать INTEGRATED/ENABLED/production accepted по чистому unit suite. Коррекция исходного контракта — до реализации через координатора, без скрытого изменения API.
