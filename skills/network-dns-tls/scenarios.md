# Диагностика по уровню отказа

Сохрани точку проверки, время, hostname, порт и протокол. Для одного URL проверяй
его адрес и порт. CIDR-инвентаризация — отдельный сценарий общего admin.py;
точные JSON-аргументы — ../linux-admin/scenarios.md.

## DNS

Windows: `Resolve-DnsName example.org -Type A -ErrorAction Stop`; AAAA при IPv6.
Резолвер: `Get-DnsClientServerAddress`. Linux: `getent ahosts example.org`; при наличии
dig — `dig example.org A` и нужный тип записи. getent проверяет системное разрешение,
dig — DNS-запрос. При расхождении проверь hosts/overrides и split DNS. Один внешний
резолвер не заменяет исходную точку пользователя. DNS-отказ не доказывает отказ TCP.

## TCP

Windows: `Test-NetConnection example.org -Port 443 -InformationLevel Detailed`.
Linux при наличии nc: `nc -vz -w 3 example.org 443`. Укажи полученный IP.
Маршрут проверяй при соответствующем симптоме; ping не обязателен для HTTP.
TCP success не подтверждает сертификат, HTTP и backend.

## TLS

На хосте с OpenSSL:
`openssl s_client -connect example.org:443 -servername example.org -verify_hostname example.org -verify_return_error < /dev/null`.
Это Bash-пример; /dev/null в PowerShell не копируй. Проверь SAN/имя, сроки, цепочку,
SNI и системное время. Ошибку доверия сохраняй, проверку сертификата не отключай.
На Windows без OpenSSL для TLS используй установленный web-research и фактический
ответ ошибки; утилиту ради одного запроса не ставь.

## HTTP и reverse proxy

Прочитай ../web-research/SKILL.md: fetch/http/browser по задаче. Сохрани URL,
status, Location, редиректы, заголовки и время. 302 — редирект; 401/403 — результат
конкретного запроса; 502/504 — сигнал проверки proxy/upstream, не доказательство
причины. При доступе прочитай конфигурацию и ограниченные логи обеих сторон общим SSH.
Сопоставь upstream hostname/port, bind, Host/SNI, таймаут и фактический процесс.
Хост и контейнер могут видеть разное; точку каждого наблюдения указывай явно.

## Вывод и изменение

Сводка: `точка → DNS → TCP → TLS → HTTP → upstream`; у проверенных этапов факт и
свидетельство. Не проверенные этапы обозначь явно. До разрешённой правки сохрани
конфиг и способ возврата доступа; после повтори исходную проверку с учётом TTL/кэша.
