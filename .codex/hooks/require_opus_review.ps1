# Stop hook: enforce per-commit Opus review for active roadmap work.
# ASCII-only on purpose (Windows PowerShell 5.1 reads .ps1 in the ANSI codepage,
# so non-ASCII here would corrupt the script). Russian guidance lives in the
# active roadmap and .claude/hooks/review_rubric.md, read by the Opus subagent.
#
# PATH-independent: uses only git + a verdict file the Opus reviewer writes.
# Active while any tracked spec below exists; delete all of them to disable.
# (Gated on the active roadmap so deleting the old AGENT_KERNEL_PLAN does not
#  silently turn off enforcement during P9-P12.)
$ErrorActionPreference = 'SilentlyContinue'
$repo = 'D:\AIWork\Elira_AI'
$specs = @(
  (Join-Path $repo 'ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md'),
  (Join-Path $repo 'docs\AGENT_KERNEL_PLAN.md')
)
if (-not ($specs | Where-Object { Test-Path $_ })) { exit 0 }

$stopActive = $false
try {
  $raw = [Console]::In.ReadToEnd()
  if ($raw) { $stopActive = [bool]((ConvertFrom-Json $raw).stop_hook_active) }
} catch {}

Set-Location $repo
$head = (& git rev-parse HEAD 2>$null)
if (-not $head) { exit 0 }
$head = $head.Trim()
$verdict = Join-Path $repo (".claude\review\{0}.md" -f $head)

function Block([string]$reason) {
  Write-Output ((@{ decision = 'block'; reason = $reason } | ConvertTo-Json -Compress))
  exit 0
}

if (-not (Test-Path $verdict)) {
  Block ("Commit $head is not yet reviewed by Opus. Spawn an Agent(model='opus') reviewer " +
    "that runs the gates (tsc + pytest), reviews HEAD against the active roadmap " +
    "(ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md), docs/ARCHITECTURE.md and " +
    ".claude/hooks/review_rubric.md, and writes the verdict to .claude/review/$head.md " +
    "(first line 'VERDICT: PASS' or 'VERDICT: FAIL'). Do not finish the turn without a PASS.")
}

$content = Get-Content $verdict -Raw
if ($content -match 'VERDICT:\s*PASS') { exit 0 }   # reviewed and passed -> allow stop

if ($stopActive) { exit 0 }   # loop guard: already nudged this chain -> let the user step in
Block ("Opus review of commit $head = FAIL. Open .claude/review/$head.md, fix the findings, " +
  "make a new commit, and re-run the Opus review.")
