# Проверки Windows по симптомам

Локально — run_bash/PowerShell. Удалённо — общий linux-admin/admin.py, сценарий ssh:

```json
{"tool_name":"ssh_run_ps","args":{"host":"alias","script":"$PSVersionTable.PSVersion; hostname; whoami","timeout":30}}
```

Сохрани JSON и выполни `python "<skills>/linux-admin/admin.py" ssh --input args.json
--workspace "<папка задачи>"` (одна строка). Транспорт сам выполняет EncodedCommand.
windows_inventory с {"target":"alias"} — общее состояние; для одного симптома
выбери целевую команду ниже. Примеры имён замени настоящими; пользовательские строки
проверяй/экранируй перед вставкой в PowerShell/WQL.

## Служба

Готовый inspect_service.ps1, его аргументы и трактовка — service.md.

## Task Scheduler

```powershell
$taskName = 'GivenTask'
$taskPath = '\'
Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop |
    Select-Object TaskName,TaskPath,State,Principal,Actions,Triggers
Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop |
    Select-Object LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns
```

LastTaskResult=0 относится к заданию; сервис проверяй отдельно. Перед правкой
сохрани XML задания. Данные principal/arguments в отчёте освободи от секретов.

## Доступ к файлу

```powershell
$targetPath = 'C:\GivenFolder'
Get-Item -LiteralPath $targetPath -ErrorAction Stop | Select-Object FullName,Attributes
Get-Acl -LiteralPath $targetPath -ErrorAction Stop | Select-Object Owner,AccessToString
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

Сопоставь ACL с фактической учёткой процесса и elevated token; сохрани ACL до правки.
Отсутствие пути и отказ доступа — разные исходы. Everyone FullControl не является
универсальным исправлением.

## Диск

```powershell
Get-Volume | Select-Object DriveLetter,FileSystem,HealthStatus,Size,SizeRemaining
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace
```

Уточни том приложения. Сначала установи владельца кэша/логов и требования хранения;
очистку выполняй только в разрешённой области, отдельно от диагностики.
