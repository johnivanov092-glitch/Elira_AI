# Служба Windows и токен проверки


Для состояния службы, свежих событий и текущего elevated token запускай готовый
сценарий: `powershell.exe -NoProfile -NonInteractive -File "<skills>/windows-admin/inspect_service.ps1" -ServiceName EventLog -Minutes 15 -OutputPath "<папка задачи>/service.json"`.
Вывод — JSON с временем, командами, service/process/events и ошибкой чтения событий.
Сохрани результат в файл задачи и прочитай ok; код 1 означает неполную/ошибочную
проверку, а не падение службы. OutputPath сохраняет UTF-8 без BOM и LF; не используй
ASCII для JSON с русским DisplayName/Message. Не создавай заново P/Invoke или
большой сборщик для этих данных. Elevated token здесь относится к процессу проверки;
токен чужого PID исследуй только по отдельному требованию, не расширяй задачу.
integrity_sid/level получены из whoami текущего токена; elevated_admin_token —
проверка активной роли текущего токена, не членства учётки в локальной группе.
Для этого запроса успешного сценария достаточно: сохраняй результат и выдавай файл,
не дублируй проверку самодельным GetTokenInformation/P/Invoke. Неопределённый
integrity отмечается явно и не превращается в Untrusted/Low по умолчанию.
Удалённо содержимое готового скрипта передавай через ssh_run_ps.
Фильтр событий ограничен 200 записями; пустой список не доказывает отсутствие любых
событий. Дополнительные диагностические команды по необходимости:

```powershell
$serviceName = 'Spooler'
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if ($null -eq $service) { throw "Service not found: $serviceName" }
$service | Select-Object Name,State,StartMode,ProcessId,PathName,StartName,ExitCode
if ($service.ProcessId -gt 0) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$($service.ProcessId)" |
        Select-Object ProcessId,ExecutablePath,CreationDate
}
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddMinutes(-15); ProviderName='Service Control Manager'} -MaxEvents 20 |
    Select-Object TimeCreated,Id,LevelDisplayName,Message
```

Нет событий — не доказательство ни отказа, ни исправности. Сообщения могут содержать
приватные данные. До разрешённой правки сохрани start mode/account/dependencies;
после проверь PID и прикладной health. Вторую копию службы напрямую не запускай.
