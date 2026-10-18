# Elira AI

Local AI workspace: Tauri 2, React/Vite/TypeScript and FastAPI/Python. The client
PC owns tools, conversations, resources and orchestration. A LAN server supplies
OpenAI-compatible local inference. Start with [the documentation index](docs/README.md)
and [repository working rules](AGENTS.md).

## Source and installed application

Normal Windows entry point: `Elira.bat`. When Windows Foundation is registered,
the launcher uses its protected client. Without Foundation it can use the
configured legacy release supervisor or development fallback.

Editing, merging or pulling this checkout does not update a separately installed
release. The release workflow and proof requirements are in
[RELEASE_LIFECYCLE.md](docs/RELEASE_LIFECYCLE.md).

## Development setup

The current Windows development environment uses Python 3.10. The following
commands assume a working Python on PATH, Node/npm, and a backend development
environment with the test packages needed for the selected tests:

```powershell
cd D:\AIWork\Elira_AI
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm ci
npm --prefix frontend ci
```

`backend/requirements.txt` is the current bounded core dependency list.
`requirements-optional.txt` adds the listed optional application integrations.
Browser/document/admin skill dependencies belong to their own skill environment
and requirements, not to an assumed second builtin runtime.

`backend/requirements.lock` is an older pinned snapshot: it currently omits the
`pywinpty`/`pyte` entries present in `requirements.txt`. It is not a verified lock
for the current core and must not be advertised as its reproducible installer.
Updating dependency pins is separate from this documentation change.

Do not link a disposable worktree's dependency directories to the live checkout
with Windows junctions. Give it its own environments or invoke an external
interpreter explicitly; cleanup of a linked tree can affect the target files.

For PDF rendering, keep the complete Poppler distribution in `.runtime/poppler/`
(or an appropriate installation on PATH), including its DLLs/resources. OCR and
Web/browser dependencies follow the corresponding mutable skill instructions;
local Tesseract is not a universal application prerequisite.

## Configuration and access

Keep machine values and credentials out of Git. The primary settings and their
source owners are listed in [SERVER.md](docs/SERVER.md). Configure the actual
endpoint, model ID and authorization; example names/default URLs are not a live
connection receipt.

For direct Python startup, `app.main` loads backend `.env` and then `.env.local`
without overwriting existing environment values. BAT launchers also parse these
files according to their own code. Avoid duplicate keys across the two files;
verify the effective process configuration when changing the launch path.
`ELIRA_CONFIG_ROOT` can select another backend configuration directory.

API authentication is implemented in `backend/app/core/auth.py`. Non-local
callers use `ELIRA_API_TOKEN` (or the generated, ignored `data/elira_api_token`).
The LAN UI uses the matching `VITE_ELIRA_API_TOKEN`. Browser Origin checks also
apply; loopback alone is not a promise that an arbitrary web page is trusted.
`ELIRA_API_AUTH=off` explicitly disables enforcement for a configured trusted setup.

## Development launchers

| Entry point | Purpose |
| --- | --- |
| `Elira.bat` | Normal application/release entry point |
| `Elira_Mobile.bat` | LAN/mobile launcher |
| `kill_elira.bat` | Existing process-stop helper |
| `scripts\run_tauri_dev.bat` | Backend preflight/start and Tauri development |
| `scripts\build_exe.bat` | Tauri installer build |
| `scripts\Elira_Autonomy_Test.bat` | Historical isolated autonomy-trial launcher |

For browser development, start these in separate terminals:

```powershell
cd D:\AIWork\Elira_AI\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd D:\AIWork\Elira_AI
npm --prefix frontend run dev
```

The Tauri development wrapper owns its backend startup and sets the existing
external-backend flag. It is not a release activation command.

## Verification

Use Atlas and focused tests while work has open questions. The final repository
gates from [AGENTS.md](AGENTS.md) run in this order; full pytest is last:

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

A failed check remains a failure in the report. Build success does not certify a
live model task, native UI acceptance or installed-release admission.
[AGENT_EVALS.md](docs/AGENT_EVALS.md) distinguishes these forms of evidence.

## Repository layout

[PROJECT_MAP.md](docs/PROJECT_MAP.md) maps current owners. Keep the root for
entry points, instructions and tool/build configuration; docs live in `docs/`,
helpers in `scripts/`, visual sources in `assets/`, history in `docs/archive/`.

Runtime DATA, journals, uploads, secrets, dependencies, caches and model weights
are intentionally ignored. A clean Git status does not mean those folders are
empty. A worktree or old branch name does not prove unmerged work: compare current
refs, ancestry and diffs against `main` before reporting it.

Work in a named branch in a separate worktree; do not switch the live checkout
away from `main`. Commit/merge/push follow the user's authorization. Release
activation has its separate confirmation contract.
