<#
Installs the isolated Windows host. Application data and user accounts stay in
place. Run from an elevated PowerShell. Limited is the default application token;
Administrator explicitly preserves the elevated interactive user's rights.
#>
[CmdletBinding()]
param(
    [ValidateSet('EliraFoundation', 'EliraFoundationProof')]
    [string]$ServiceName = 'EliraFoundation',
    [string]$Platform = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonHome = 'C:\Program Files\Python310',
    [string]$DataDir = '',
    [string]$AgentRunsDir = '',
    [int]$Port = 8000,
    [ValidateSet('limited', 'administrator')]
    [string]$ApplicationTokenMode = 'limited',
    [switch]$ValidateOnly,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
}

function Set-OwnedAcl([string]$Path, [string]$Sddl) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm($Sddl)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Assert-NoReparse([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Installation path contains a reparse point: $($item.FullName)"
        }
        $item = $item.Parent
    }
}

function Invoke-Sc([string[]]$Arguments) {
    $output = & "$env:SystemRoot\System32\sc.exe" @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "sc $($Arguments[0]) failed: $output" }
    Write-Verbose ($output | Out-String)
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Foundation installation requires an elevated PowerShell. Desktop/Agent do not.'
}
$userSid = $identity.User.Value
if ($userSid -in @('S-1-5-18', 'S-1-5-19', 'S-1-5-20')) {
    throw 'Run installation from the intended interactive user account, elevated through UAC.'
}
$Platform = (Resolve-Path -LiteralPath $Platform).Path
$PythonHome = (Resolve-Path -LiteralPath $PythonHome).Path
Assert-NoReparse $Platform
Assert-NoReparse $PythonHome
# Binding a default over an existing override would silently hide chat/history.
# Require explicit paths in that case; do not execute application dotenv/code as admin.
function Resolve-UserStore([string]$Explicit, [string]$Variable, [string]$Relative) {
    if (-not $Explicit) {
        $configured = [Environment]::GetEnvironmentVariable($Variable)
        foreach ($dotenv in @((Join-Path $Platform 'backend\.env'), (Join-Path $Platform 'backend\.env.local'))) {
            if (Test-Path -LiteralPath $dotenv -PathType Leaf) {
                foreach ($line in [IO.File]::ReadAllLines($dotenv, [Text.Encoding]::UTF8)) {
                    if ($line -match ('^\s*(?:export\s+)?' + [regex]::Escape($Variable) + '\s*=')) {
                        $configured = 'present'
                    }
                }
            }
        }
        if ($configured) { throw "$Variable is configured. Supply its existing absolute path explicitly to the installer." }
        $Explicit = Join-Path $Platform $Relative
    }
    if (-not [IO.Path]::IsPathRooted($Explicit)) { throw "$Variable requires an absolute path." }
    $resolved = [IO.Path]::GetFullPath($Explicit)
    $existing = $resolved
    while (-not (Test-Path -LiteralPath $existing)) {
        $parent = Split-Path -Parent $existing
        if (-not $parent -or $parent -eq $existing) { throw "Cannot validate parent of $Variable." }
        $existing = $parent
    }
    Assert-NoReparse $existing
    return $resolved
}
$DataDir = Resolve-UserStore $DataDir 'ELIRA_DATA_DIR' 'data'
$AgentRunsDir = Resolve-UserStore $AgentRunsDir 'ELIRA_AGENT_RUNS_DIR' '.agent\runs'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
$proof = $ServiceName -eq 'EliraFoundationProof'
if ($proof -and $ApplicationTokenMode -ne 'limited') {
    throw 'The fixed proof service validates limited tokens only.'
}
if ($proof -and ($Port -eq 8000 -or $Platform -eq (Split-Path -Parent $PSScriptRoot))) {
    throw 'Proof installation requires an explicit isolated test Platform and a port other than 8000.'
}
$programFiles = [Environment]::GetFolderPath('ProgramFiles')
$programData = [Environment]::GetFolderPath('CommonApplicationData')
$installRoot = Join-Path $programFiles $ServiceName
$stateRoot = Join-Path $programData $ServiceName
Assert-NoReparse $programFiles
Assert-NoReparse $programData
if ((Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -or
    (Test-Path -LiteralPath $installRoot) -or (Test-Path -LiteralPath $stateRoot)) {
    throw 'Existing installation detected. No files or service configuration were overwritten.'
}
$modules = @('foundation_service.py', 'foundation_windows.py', 'foundation_client.py', 'foundation_storage.py', 'elira_release.py')
if ($proof) { $modules += 'foundation_fixture.py' }
foreach ($module in $modules) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $module) -PathType Leaf)) {
        throw "Required Foundation module is missing: $module"
    }
}
$python = Join-Path $PythonHome 'python.exe'
$runtimeInfo = & $python -I -S -B -c 'import json,sys; print(json.dumps({"version":list(sys.version_info[:3]),"bits":64 if sys.maxsize>2**32 else 32}))'
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the trusted Python runtime.' }
$runtimeInfo = $runtimeInfo | ConvertFrom-Json
if ($runtimeInfo.bits -ne 64 -or $runtimeInfo.version[0] -ne 3 -or $runtimeInfo.version[1] -ne 10) {
    throw 'This installer is validated for the existing 64-bit CPython 3.10 runtime.'
}
if ($ValidateOnly) {
    [pscustomobject]@{
        validated = $true
        service = $ServiceName
        platform = $Platform
        data = $DataDir
        journals = $AgentRunsDir
        application_token_mode = $ApplicationTokenMode
        install_root = $installRoot
        state_root = $stateRoot
        changes_applied = $false
    } | ConvertTo-Json -Depth 4
    return
}

# New directories get protected ownership before any executable/config is copied.
# Root's filtered token has no enabled Administrators SID and cannot change them.
$null = New-Item -ItemType Directory -Path $installRoot
Set-OwnedAcl $installRoot 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;BU)'
$null = New-Item -ItemType Directory -Path $stateRoot
Set-OwnedAcl $stateRoot 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
$runtimeRoot = Join-Path $installRoot 'python'
$modulesRoot = Join-Path $installRoot 'host'
$null = New-Item -ItemType Directory -Path $runtimeRoot, $modulesRoot
foreach ($file in (Get-ChildItem -LiteralPath $PythonHome -File)) {
    if ($file.Extension -in @('.exe', '.dll') -or $file.Name -eq 'LICENSE.txt') {
        Copy-Item -LiteralPath $file.FullName -Destination $runtimeRoot
    }
}
foreach ($directory in @('DLLs', 'Lib')) {
    $source = Join-Path $PythonHome $directory
    $destination = Join-Path $runtimeRoot $directory
    # /COPY:DAT deliberately does not copy source ACL/owner. No purge/mirror/delete.
    & "$env:SystemRoot\System32\robocopy.exe" $source $destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD site-packages __pycache__ /XF *.pyc *.pyo | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Private Python copy failed: $directory" }
}
Write-Utf8 (Join-Path $runtimeRoot 'python310._pth') "Lib`nDLLs`n..\host`n"
foreach ($module in $modules) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $module) -Destination $modulesRoot
}
$privatePython = Join-Path $runtimeRoot 'python.exe'
$probe = & $privatePython -I -S -B -c 'import ctypes,sqlite3,json,sys; assert not any("site-packages" in p for p in sys.path); print(json.dumps({"executable":sys.executable,"path":sys.path}))'
if ($LASTEXITCODE -ne 0) { throw 'Private runtime isolation/import check failed.' }

$serviceScript = Join-Path $modulesRoot 'foundation_service.py'
$configPath = Join-Path $stateRoot 'installation.json'
$imagePath = '"' + $privatePython + '" -I -S -B "' + $serviceScript + '" --config "' + $configPath + '"'
if ($proof) {
    $imagePath = '"' + $privatePython + '" -I -S -B "' + (Join-Path $modulesRoot 'foundation_windows.py') + '" --proof-service ' + $ServiceName + ' ' + $userSid
}
$startMode = if ($proof) { 'demand' } else { 'auto' }
Invoke-Sc @('create', $ServiceName, 'binPath=', $imagePath, 'start=', $startMode, 'obj=', 'NT AUTHORITY\LocalService', 'DisplayName=', $ServiceName)
Invoke-Sc @('sidtype', $ServiceName, 'unrestricted')
Invoke-Sc @('privs', $ServiceName, 'SeChangeNotifyPrivilege/SeImpersonatePrivilege/SeAssignPrimaryTokenPrivilege/SeIncreaseQuotaPrivilege')
Invoke-Sc @('description', $ServiceName, "Elira release state and recovery; authenticated interactive application token: $ApplicationTokenMode.")
# Ordinary users may query only. Application close/start uses authenticated IPC.
Invoke-Sc @('sdset', $ServiceName, 'D:P(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCRC;;;BU)')
Invoke-Sc @('failure', $ServiceName, 'reset=', '86400', 'actions=', 'restart/5000/restart/15000/restart/60000')
$serviceSid = ([Security.Principal.NTAccount]::new('NT SERVICE', $ServiceName)).Translate([Security.Principal.SecurityIdentifier]).Value
Set-OwnedAcl $installRoot "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;BU)(A;OICI;GRGX;;;$serviceSid)"
Set-OwnedAcl $stateRoot "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;$serviceSid)"
$published = Join-Path $stateRoot 'published'
$null = New-Item -ItemType Directory -Path $published
Set-OwnedAcl $published "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;$serviceSid)(A;OICI;GRGX;;;$userSid)"

$config = [ordered]@{
    version = 1
    service_name = $ServiceName
    pipe_name = $ServiceName + '.v1'
    user_sid = $userSid
    service_sid = $serviceSid
    platform = $Platform
    store = $stateRoot
    published = $published
    candidates = (Join-Path $Platform '.runtime\releases\candidates')
    data = $DataDir
    journals = $AgentRunsDir
    python = $privatePython
    port = $Port
    diagnostic = $proof
    application_token_mode = $ApplicationTokenMode
}
Write-Utf8 $configPath (($config | ConvertTo-Json -Depth 4) + "`n")
if ($proof) {
    $records = Join-Path $stateRoot 'records'
    $null = New-Item -ItemType Directory -Path $records
    foreach ($canary in @((Join-Path $installRoot 'acl-canary.txt'), (Join-Path $stateRoot 'acl-canary.txt'), (Join-Path $records 'acl-canary.txt'))) {
        Write-Utf8 $canary "foundation-acl-canary`n"
    }
}
$manifest = @{}
Get-ChildItem -LiteralPath $installRoot -Recurse -File | ForEach-Object {
    $manifest[$_.FullName.Substring($installRoot.Length + 1)] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
Write-Utf8 (Join-Path $stateRoot 'installation-manifest.json') (($manifest | ConvertTo-Json -Depth 4) + "`n")
# Copied/created descendants must not be owned by the interactive account:
# ownership itself grants WRITE_DAC even when its ordinary write ACE is absent.
foreach ($ownedRoot in @($installRoot, $stateRoot)) {
    $resolved = (Resolve-Path -LiteralPath $ownedRoot).Path
    if ($resolved -ne $ownedRoot) { throw 'Unexpected resolved installation root.' }
    Assert-NoReparse $resolved
    & "$env:SystemRoot\System32\icacls.exe" $resolved /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot set protected installation ownership.' }
}

$registry = 'HKLM:\SOFTWARE\Elira\' + $ServiceName
$null = New-Item -Path $registry -Force
$null = New-ItemProperty -LiteralPath $registry -Name InstallRoot -Value $installRoot -PropertyType String -Force
$null = New-ItemProperty -LiteralPath $registry -Name ServiceName -Value $ServiceName -PropertyType String -Force
$null = New-ItemProperty -LiteralPath $registry -Name ApplicationTokenMode -Value $ApplicationTokenMode -PropertyType String -Force
$null = New-ItemProperty -LiteralPath $registry -Name Platform -Value $Platform -PropertyType String -Force
$null = New-ItemProperty -LiteralPath $registry -Name Port -Value $Port -PropertyType DWord -Force
foreach ($binding in @{
    StateRoot = $stateRoot
    Candidates = $config.candidates
    Published = $published
    Data = $DataDir
    Journals = $AgentRunsDir
}.GetEnumerator()) {
    $null = New-ItemProperty -LiteralPath $registry -Name $binding.Key -Value $binding.Value -PropertyType String -Force
}
$registryAcl = [Security.AccessControl.RegistrySecurity]::new()
$registryAcl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;CI;KA;;;SY)(A;CI;KA;;;BA)(A;CI;KR;;;BU)')
# Registry provider's Set-Acl requires -Path (the exact key has no wildcards).
Set-Acl -Path $registry -AclObject $registryAcl
Set-Acl -Path 'HKLM:\SOFTWARE\Elira' -AclObject $registryAcl
if ($Start) { Start-Service -Name $ServiceName }
[pscustomobject]@{
    service = $ServiceName
    account = 'NT AUTHORITY\LocalService'
    user_sid = $userSid
    install_root = $installRoot
    state_root = $stateRoot
    running = [bool]$Start
    application_data_migrated = $false
    application_token_mode = $ApplicationTokenMode
    runtime_probe = ($probe | ConvertFrom-Json)
} | ConvertTo-Json -Depth 4
