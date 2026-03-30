param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$BackendPython,
    [int]$Port = 8000,
    [switch]$AutoStopEliraBackend
)

$ErrorActionPreference = "Stop"

function Normalize-PathValue {
    param([string]$Value)
    try {
        return [System.IO.Path]::GetFullPath($Value).TrimEnd('\')
    } catch {
        return $Value
    }
}

$repoPath = Normalize-PathValue $RepoRoot
$pythonPath = Normalize-PathValue $BackendPython

function Test-EliraHealth {
    param([int]$TargetPort)
    try {
        $response = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $TargetPort + "/health") -Method Get -TimeoutSec 2 -ErrorAction Stop
        if ($response -and $response.service -eq "elira-ai-api") {
            return $true
        }
    } catch {
    }
    return $false
}

$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if (-not $listeners -or $listeners.Count -eq 0) {
    Write-Host "[INFO] Port $Port is free."
    exit 0
}

$repoLower = $repoPath.ToLowerInvariant()
$pythonLower = $pythonPath.ToLowerInvariant()
$healthIsElira = Test-EliraHealth -TargetPort $Port
$repoBackendFound = $false
$foreignProcess = $null
$repoBackendPid = $null

foreach ($listener in $listeners) {
    $pidValue = [int]$listener.OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    $rawExePath = ""
    $rawCmdLine = ""
    if ($proc) {
        if ($null -ne $proc.ExecutablePath) {
            $rawExePath = [string]$proc.ExecutablePath
        }
        if ($null -ne $proc.CommandLine) {
            $rawCmdLine = [string]$proc.CommandLine
        }
    }
    $exePath = Normalize-PathValue $rawExePath
    $cmdLine = $rawCmdLine
    $cmdLower = $cmdLine.ToLowerInvariant()

    $isRepoBackend = $false
    if ($exePath -and $exePath.ToLowerInvariant() -eq $pythonLower) {
        $isRepoBackend = $true
    }
    if (-not $isRepoBackend -and $cmdLower.Contains($repoLower) -and $cmdLower.Contains("uvicorn") -and $cmdLower.Contains("app.main:app")) {
        $isRepoBackend = $true
    }
    if (-not $isRepoBackend -and $healthIsElira -and $cmdLower.Contains("uvicorn") -and $cmdLower.Contains("app.main:app")) {
        $isRepoBackend = $true
    }

    if ($isRepoBackend) {
        $repoBackendFound = $true
        $repoBackendPid = $pidValue
        continue
    }

    $foreignProcess = [pscustomobject]@{
        Pid = $pidValue
        ExecutablePath = $exePath
        CommandLine = $cmdLine
    }
    break
}

if ($foreignProcess) {
    Write-Host "[ERROR] Port $Port is already occupied by a different process."
    Write-Host ("[ERROR] PID: " + $foreignProcess.Pid)
    if ($foreignProcess.ExecutablePath) {
        Write-Host ("[ERROR] Executable: " + $foreignProcess.ExecutablePath)
    }
    if ($foreignProcess.CommandLine) {
        Write-Host ("[ERROR] Command: " + $foreignProcess.CommandLine)
    }
    Write-Host "[HINT] Stop the conflicting process or move it off port 8000, then retry."
    exit 20
}

if ($repoBackendFound) {
    if ($AutoStopEliraBackend -and $repoBackendPid) {
        try {
            Stop-Process -Id $repoBackendPid -Force -ErrorAction Stop
            Write-Host ("[INFO] Stopped existing Elira backend on port " + $Port + " (PID " + $repoBackendPid + ").")
            Start-Sleep -Milliseconds 400
            exit 11
        } catch {
            Write-Host ("[ERROR] Failed to stop existing Elira backend PID " + $repoBackendPid + ".")
            Write-Host ("[ERROR] " + $_.Exception.Message)
            exit 21
        }
    }
    Write-Host "[INFO] Reusing existing Elira backend on port $Port."
    exit 10
}

Write-Host "[ERROR] Port $Port is busy, but the owning process could not be classified."
exit 20
