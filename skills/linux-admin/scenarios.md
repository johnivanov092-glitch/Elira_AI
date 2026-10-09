# Общие SSH и IT Ops сценарии

Одна реализация транспорта: ssh_runtime.py. Windows, сеть и контейнеры используют
этот же admin.py; не копируй SSH-клиент в другие навыки.

`python <skills>/linux-admin/admin.py <сценарий> --input args.json --workspace <папка>`.

| Сценарий | JSON аргументы |
|---|---|
| hosts | {} |
| ssh | {"tool_name":"ssh_run","args":{"host":"alias","command":"uname -a"}} |
| ssh | {"tool_name":"ssh_run_ps","args":{"host":"alias","script":"Get-Service"}} |
| ssh | tool_name также ssh_read, ssh_write, ssh_replace, ssh_port_check; параметры в ssh_runtime.py |
| ssh_healthcheck / linux_inventory / windows_inventory | {"target":"alias"} |
| network_inventory | смотри сигнатуру tool_itops_network_inventory в itops_runtime.py |
| systemd_service_inspect / config_inspect / database_inspect | именованные цели и параметры в itops_runtime.py |
| mikrotik_inventory | {"router_id":"id"} |
| registry | сохранённые активы и подключения; параметры в registry.py |

Проверяй ok, exit_code и свидетельства. Длительные SSH-команды должны возвращать
управляемое фоновое задание; наблюдение/остановка — существующий run_server.
Не выдавай обрыв SSH за доказанную остановку удалённого процесса.

Начни с сценария registry или hosts. Для известного типа цели используй сценарий
healthcheck/inventory; произвольную команду передавай сценарию ssh через --input.
Перед разрушительным изменением подготовь резервную копию, затем проверь результат.

Список сохранённых целей без подключения: `python "<skills>/linux-admin/admin.py" hosts --workspace "<папка задачи>"`.
У hosts нет обязательных аргументов: --input можно не передавать, не указывай
несуществующий JSON-файл. ok=true и hosts=[] — успешный пустой список сохранённых
целей, а не ошибка запуска. Не читай исходники/конфиги для повторного доказательства
пустого списка; отдельная инвентаризация ~/.ssh нужна, когда её просит пользователь.

## Настройка SSH

НАСТРОЙКА SSH-ДОСТУПА. Сценарий ssh принимает любой непустой host или алиас OpenSSH; `data/ssh_acl.json` хранит только подсказки/избранное и ничего не разрешает. Когда просят создать/найти/напомнить SSH-доступ, отдай пользователю СВЯЗНУЮ пару: (1) блок `Host <алиас>` для `~/.ssh/config` (HostName/User/IdentityFile) и (2) тот же `<алиас>` как удобное имя для сценария ssh. Публичный ключ (`.pub`) — пользователю для установки на таргет; приватный ключ в чат НИКОГДА (в конфиге он по пути). Инвентарь бери из `~/.ssh` (ключи, `config`, `known_hosts`) + сохранённые SSH favorites. PowerShell ЧЕРЕЗ ssh шли как `powershell -EncodedCommand <base64>` — иначе cmd.exe клиента рвёт `|`/кавычки до отправки.
