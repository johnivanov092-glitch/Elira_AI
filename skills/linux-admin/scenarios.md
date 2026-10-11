# Общие SSH и IT Ops сценарии

Одна реализация транспорта: ssh_runtime.py. Windows, сеть и контейнеры используют
этот же admin.py; не копируй SSH-клиент в другие навыки.

`python <skills>/linux-admin/admin.py <сценарий> --input args.json --workspace <папка>`.

| Сценарий | JSON аргументы |
|---|---|
| hosts | {} |
| ssh | {"tool_name":"ssh_run","args":{"host":"alias","command":"uname -a"}} |
| ssh | {"tool_name":"ssh_run_ps","args":{"host":"alias","script":"Get-Service"}} |
| ssh | ssh_read: {"tool_name":"ssh_read","args":{"host":"alias","path":"/path/file","max_chars":4000}} |
| ssh | ssh_write: {"tool_name":"ssh_write","args":{"host":"alias","path":"/path/file","content":"new text","append":false}} |
| ssh | ssh_replace: {"tool_name":"ssh_replace","args":{"host":"alias","path":"/path/file","old":"before","new":"after"}} |
| ssh | ssh_port_check: {"tool_name":"ssh_port_check","args":{"host":"alias","port":443}} — проверяет удалённый listener/PID; доступность из клиента проверяй отдельно |
| ssh_healthcheck / linux_inventory / windows_inventory | {"target":"alias"} |
| network_inventory | {"cidr":"192.0.2.1/32","ports":[443],"connect_timeout":1.0,"concurrency":4} — замени адрес заданной целью |
| systemd_service_inspect | {"target":"alias","unit":"nginx.service"} — настоящая .service, только чтение |
| config_inspect | {"target":"alias","config_id":"netdata-main"} — поддерживаемый именованный конфиг |
| database_inspect | {"database_id":"id"} — фактический зарегистрированный id |
| mikrotik_inventory | {"router_id":"id"} |
| registry | {"action":"list"} — сохранённые активы; другие действия по фактическому registry.py |

Проверяй ok, exit_code и свидетельства. Длительные SSH-команды должны возвращать
управляемое фоновое задание; наблюдение/остановка — существующий run_server.
Не выдавай обрыв SSH за доказанную остановку удалённого процесса.

Если цель неизвестна, начни с registry или hosts. Указанный alias/hostname/IP
передавай прямо в целевой сценарий. healthcheck/inventory — для общего состояния;
произвольную диагностическую команду передавай сценарию ssh через --input.
Перед разрушительным изменением подготовь резервную копию, затем проверь результат.

Список сохранённых целей без подключения: `python "<skills>/linux-admin/admin.py" hosts --workspace "<папка задачи>"`.
У hosts нет обязательных аргументов: --input можно не передавать, не указывай
несуществующий JSON-файл. ok=true и hosts=[] — успешный пустой список сохранённых
целей, а не ошибка запуска. Не читай исходники/конфиги для повторного доказательства
пустого списка; отдельная инвентаризация ~/.ssh нужна, когда её просит пользователь.

## Подробности по задаче

- Служба, диск, inode, неизвестный исход: diagnostics.md.
- Создание/поиск/напоминание SSH-доступа и ключи: ssh-access.md.
- Не читай все ветки: выбери заданную задачу. Исполнение остаётся через этот admin.py.
