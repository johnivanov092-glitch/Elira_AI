[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$ServiceName,
    [ValidateRange(1, 1440)]
    [int]$Minutes = 15,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $groups = & whoami.exe /groups /fo csv /nh
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read current token groups with whoami' }
    $integrityMatches = [regex]::Matches(($groups -join "`n"), '\bS-1-16-(\d+)\b')
    $integritySid = $null
    $integrityLevel = 'not determined'
    if ($integrityMatches.Count -eq 1) {
        $integritySid = $integrityMatches[0].Value
        $rid = [int]$integrityMatches[0].Groups[1].Value
        $levels = @{ 0 = 'Untrusted'; 4096 = 'Low'; 8192 = 'Medium'; 8448 = 'MediumPlus'; 12288 = 'High'; 16384 = 'System'; 20480 = 'Protected' }
        $integrityLevel = if ($levels.ContainsKey($rid)) { $levels[$rid] } else { "Unknown($rid)" }
    }
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if ($null -eq $service) { throw "Service not found: $ServiceName" }
    $process = $null
    if ($service.ProcessId -gt 0) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($service.ProcessId)" |
            Select-Object ProcessId,ExecutablePath,CreationDate
    }
    $events = @()
    $eventError = $null
    $since = (Get-Date).AddMinutes(-$Minutes)
    try {
        $recent = @(Get-WinEvent -FilterHashtable @{
            LogName = 'System'
            StartTime = $since
            ProviderName = @('Service Control Manager', 'Microsoft-Windows-Eventlog')
        } -MaxEvents 200 -ErrorAction Stop)
        $servicePattern = [regex]::Escape($ServiceName) + '|' + [regex]::Escape($service.DisplayName)
        $events = @($recent | Where-Object {
            ($ServiceName -eq 'EventLog' -and $_.ProviderName -eq 'Microsoft-Windows-Eventlog') -or
            $_.Message -match $servicePattern
        } | Select-Object TimeCreated,Id,ProviderName,LevelDisplayName,Message)
    } catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
            $eventError = $_.Exception.Message
        }
    }
    $result = [ordered]@{
        ok = ($null -eq $eventError)
        checked_at = (Get-Date).ToString('o')
        hostname = [Environment]::MachineName
        powershell_version = $PSVersionTable.PSVersion.ToString()
        identity = $identity.Name
        elevated_admin_token = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        integrity_sid = $integritySid
        integrity_level = $integrityLevel
        service = ($service | Select-Object Name,DisplayName,State,StartMode,ProcessId,PathName,StartName,ExitCode)
        process = $process
        events_since = $since.ToString('o')
        events_limit = 200
        events = $events
        event_query_error = $eventError
        commands = @(
            "Get-CimInstance Win32_Service -Filter Name='$ServiceName'",
            "Get-CimInstance Win32_Process -Filter ProcessId=$($service.ProcessId)",
            "WindowsPrincipal.IsInRole(Administrator) for current token",
            "whoami.exe /groups /fo csv /nh (current mandatory integrity SID)",
            "Get-WinEvent System, Service Control Manager/Microsoft-Windows-Eventlog, since $($since.ToString('o')), max 200"
        )
    }
    $json = ($result | ConvertTo-Json -Depth 6).Replace("`r`n", "`n") + "`n"
    if ($OutputPath) {
        $destination = [System.IO.Path]::GetFullPath($OutputPath)
        if ($destination -eq [System.IO.Path]::GetFullPath($PSCommandPath)) {
            throw 'Output would overwrite this script'
        }
        [System.IO.File]::WriteAllText($destination, $json, [System.Text.UTF8Encoding]::new($false))
    }
    Write-Output $json
    if ($null -ne $eventError) { exit 1 }
} catch {
    [ordered]@{ ok = $false; error = $_.Exception.Message; service_name = $ServiceName } |
        ConvertTo-Json -Depth 3
    exit 1
}
