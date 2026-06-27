"""
plugin_system.py — hardened plugin system for Elira AI (P9.1).

Discovery reads metadata ONLY from <plugin>.manifest.json.
No plugin .py is ever imported into the backend process.
All plugin code executes in a child subprocess via JSON I/O.

Manifest schema (fields used by this module):
  name, version, enabled, timeout, capabilities,
  category, icon, description, author, triggers,
  hooks (list of hook names: "on_start"|"on_message"|"on_response"),
  config (dict of default settings)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from app.core.config import DATA_DIR

logger = logging.getLogger(__name__)

PLUGINS_DIR = DATA_DIR / "plugins"
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

_RUNNER_SCRIPT = Path(__file__).parent / "_subprocess_runner.py"
_BACKEND_ROOT = str(Path(__file__).parents[3])  # .../backend
PLUGIN_DEFAULT_TIMEOUT: int = 30    # seconds
PLUGIN_MIN_TIMEOUT_SECONDS: int = 1
PLUGIN_MAX_TIMEOUT_SECONDS: int = 120
PLUGIN_MAX_OUTPUT_CHARS: int = 50_000

# Only these lifecycle hook names may be invoked via fire_hook or the runner.
_ALLOWED_HOOKS: frozenset[str] = frozenset({"on_start", "on_message", "on_response"})

# Hard byte limits for subprocess output reading; prevents memory DoS.
# Reads are incremental — the process is killed as soon as a limit is crossed.
_STDOUT_MAX_BYTES: int = PLUGIN_MAX_OUTPUT_CHARS * 3   # 3 bytes/char (UTF-8 headroom)
_STDERR_MAX_BYTES: int = 2_000

# Allowlist of env vars passed to plugin subprocesses.
# Application secrets (DB URLs, API keys) use custom names and are excluded.
_SUBPROCESS_ENV_PASSTHROUGH = frozenset({
    # Windows essentials
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    # Executable resolution
    "PATH", "PATHEXT",
    # Temp dirs
    "TEMP", "TMP",
    # Python runtime
    "PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME",
    # User profile (needed by Python on Windows for site-packages)
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA",
    # Unix/macOS equivalents
    "HOME", "USER", "LANG", "LC_ALL",
})


def _make_subprocess_env() -> dict[str, str]:
    """Build a minimal environment for plugin subprocesses."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _SUBPROCESS_ENV_PASSTHROUGH}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _normalize_timeout(raw: Any, plugin_name: str) -> int:
    """Validate and clamp a timeout value from a plugin manifest.

    Invalid type → PLUGIN_DEFAULT_TIMEOUT + warning.
    Out of [PLUGIN_MIN_TIMEOUT_SECONDS, PLUGIN_MAX_TIMEOUT_SECONDS] → clamped + warning.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Plugin '%s': invalid timeout %r — using default %ds",
            plugin_name, raw, PLUGIN_DEFAULT_TIMEOUT,
        )
        return PLUGIN_DEFAULT_TIMEOUT
    if value < PLUGIN_MIN_TIMEOUT_SECONDS:
        logger.warning(
            "Plugin '%s': timeout %ds below minimum %ds — clamping",
            plugin_name, value, PLUGIN_MIN_TIMEOUT_SECONDS,
        )
        return PLUGIN_MIN_TIMEOUT_SECONDS
    if value > PLUGIN_MAX_TIMEOUT_SECONDS:
        logger.warning(
            "Plugin '%s': timeout %ds exceeds maximum %ds — clamping",
            plugin_name, value, PLUGIN_MAX_TIMEOUT_SECONDS,
        )
        return PLUGIN_MAX_TIMEOUT_SECONDS
    return value


# ── Manifest loading ──────────────────────────────────────────────────────────

def _load_plugin_manifest(py_file: Path) -> dict | None:
    """Load <stem>.manifest.json alongside the plugin .py file.

    Returns the parsed manifest dict, or None if no manifest exists.
    """
    manifest_path = py_file.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Plugin '%s' has malformed manifest: %s", py_file.stem, exc)
        return None


# ── Subprocess runner ─────────────────────────────────────────────────────────

def _run_plugin_subprocess(
    py_file: str,
    payload: dict,
    timeout: int = PLUGIN_DEFAULT_TIMEOUT,
) -> dict:
    """Execute a plugin action out-of-process with bounded incremental reads.

    stdout and stderr are drained in threads so that:
      - The subprocess cannot deadlock by filling the OS pipe buffer.
      - Each stream has a hard byte cap; exceeding it kills the process
        immediately and returns a controlled error (no memory accumulation).
      - The direct runner process is reaped on timeout and overflow;
        child processes spawned inside the plugin are not tracked
        (plugins remain disabled by default — no OS-level sandbox here).

    payload schema:
      {"action": "run",  "args": {...}}
      {"action": "hook", "hook_name": "<name>", "data": <any>}
      {"action": "inspect"}
    """
    env = _make_subprocess_env() or None
    input_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    try:
        proc = subprocess.Popen(
            [sys.executable, str(_RUNNER_SCRIPT), py_file, _BACKEND_ROOT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PLUGINS_DIR),
            env=env,
        )
    except Exception as exc:
        logger.warning("Plugin subprocess start failed (%s): %s", py_file, exc)
        return {"ok": False, "error": "Plugin execution error"}

    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []
    _flags: dict[str, bool] = {"stdout_overflow": False, "stderr_overflow": False}

    def _write_stdin() -> None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except Exception:
            pass

    def _read_stdout() -> None:
        total = 0
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                total += len(chunk)
                if total > _STDOUT_MAX_BYTES:
                    _flags["stdout_overflow"] = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return
                stdout_buf.append(chunk)
        except Exception:
            pass

    def _read_stderr() -> None:
        total = 0
        try:
            while True:
                chunk = proc.stderr.read(1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _STDERR_MAX_BYTES:
                    _flags["stderr_overflow"] = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return
                stderr_buf.append(chunk)
        except Exception:
            pass

    t_in  = threading.Thread(target=_write_stdin,  daemon=True)
    t_out = threading.Thread(target=_read_stdout,  daemon=True)
    t_err = threading.Thread(target=_read_stderr,  daemon=True)
    t_in.start()
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        timed_out = True

    t_in.join(timeout=1.0)
    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)

    if timed_out:
        return {"ok": False, "error": f"Plugin timed out after {timeout}s"}

    if _flags["stdout_overflow"]:
        return {"ok": False, "error": f"Plugin stdout exceeded {PLUGIN_MAX_OUTPUT_CHARS} char limit"}

    if _flags["stderr_overflow"]:
        snippet = b"".join(stderr_buf).decode("utf-8", errors="replace")[:200]
        logger.warning("Plugin stderr overflow (%s): %.200s…", py_file, snippet)
        return {"ok": False, "error": f"Plugin stderr exceeded {_STDERR_MAX_BYTES} byte limit"}

    stdout = b"".join(stdout_buf).decode("utf-8", errors="replace").strip()
    stderr = b"".join(stderr_buf).decode("utf-8", errors="replace").strip()

    # Byte cap allows UTF-8 headroom; enforce the exact char limit after decode.
    if len(stdout) > PLUGIN_MAX_OUTPUT_CHARS:
        return {"ok": False, "error": f"Plugin output exceeded {PLUGIN_MAX_OUTPUT_CHARS} char limit"}

    if not stdout:
        if proc.returncode != 0:
            err_msg = stderr[:500] if stderr else "Plugin process exited with error"
            logger.warning("Plugin non-zero exit (%s, rc=%d): %s", py_file, proc.returncode, err_msg)
            return {"ok": False, "error": err_msg}
        return {"ok": False, "error": "Plugin produced no output"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Plugin output is not valid JSON: {exc}"}


_CONFIG_FILE = DATA_DIR / "plugins_config.json"

_plugins: dict[str, dict] = {}
_plugin_states: dict[str, bool] = {}  # name → enabled/disabled


# ═══════════════════════════════════════════════════════════════
# КОНФИГ
# ═══════════════════════════════════════════════════════════════

def _load_config() -> dict:
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Plugin config load error: {e}")
    return {}


def _save_config(config: dict) -> None:
    try:
        _CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Plugin config save error: {e}")


def _set_plugin_config(name: str, data: dict) -> None:
    config = _load_config()
    config[name] = {**config.get(name, {}), **data}
    _save_config(config)


# ═══════════════════════════════════════════════════════════════
# ЗАГРУЗКА / ПЕРЕЗАГРУЗКА
# ═══════════════════════════════════════════════════════════════

def _build_plugin_record(name: str, py_file: Path, config: dict, manifest: dict) -> dict:
    """Build the _plugins registry entry from manifest.json only.

    No plugin .py is imported. All metadata is read from the manifest.
    hooks is a list of declared hook names, e.g. ["on_start", "on_message"].
    """
    plugin_conf = config.get(name, {})
    manifest_enabled = bool(manifest.get("enabled", False))
    enabled = plugin_conf.get("enabled", manifest_enabled)
    default_config = manifest.get("config", {})
    user_settings = plugin_conf.get("settings", {})
    timeout = _normalize_timeout(manifest.get("timeout", PLUGIN_DEFAULT_TIMEOUT), name)
    return {
        "path": str(py_file),
        "description": manifest.get("description") or manifest.get("name", ""),
        "author": manifest.get("author", ""),
        "version": manifest.get("version", "1.0"),
        "category": manifest.get("category", "utility"),
        "icon": manifest.get("icon", "🔌"),
        "triggers": list(manifest.get("triggers", [])),
        "hooks": list(manifest.get("hooks", [])),
        "default_config": default_config,
        "user_settings": {**default_config, **user_settings},
        "enabled": enabled,
        "capabilities": list(manifest.get("capabilities", [])),
        "timeout": timeout,
    }


def _register_plugin_in_tool_registry(name: str, info: dict) -> None:
    """Register/refresh a plugin's ToolSpec in the Tool Registry (source='plugin').

    P9.2-FIXUP: a newly discovered plugin lands forbidden + disabled +
    policy_classified=0. Plugin code is untrusted subprocess code, so it cannot run
    until an admin explicitly classifies it via the Tool API (PATCH permission +
    enabled + policy_classified). A reload only refreshes metadata — it never resets
    the admin's policy on an already-registered plugin (register_dynamic_tool).
    """
    try:
        import app.application.tool_registry.runtime as _tr

        def _handler(args: dict, _name: str = name) -> dict:
            return run_plugin(_name, args)

        _tr.register_dynamic_tool(
            name,
            _handler,
            display_name=info.get("description", name),
            display_name_ru=info.get("description", name),
            description=info.get("description", ""),
            category=info.get("category", "plugin"),
            source="plugin",
            side_effect=True,
            scopes=["shell.exec"],
            timeout_seconds=int(info.get("timeout", PLUGIN_DEFAULT_TIMEOUT) or PLUGIN_DEFAULT_TIMEOUT),
        )
    except Exception as exc:
        logger.warning(f"Plugin '{name}' tool-registry registration failed: {exc}")


def _unregister_plugin_from_tool_registry(name: str) -> None:
    """Remove a plugin entry from the Tool Registry when it's deleted."""
    try:
        import app.application.tool_registry.runtime as _tr
        _tr.delete_tool(name)
    except Exception as exc:
        logger.warning(f"Plugin '{name}' tool-registry removal failed: {exc}")


def load_plugins() -> dict:
    """Discover plugins from data/plugins/ by reading manifest.json only.

    No plugin .py is imported into the backend process.
    on_start hooks run in a child subprocess if the plugin is enabled.
    """
    global _plugins, _plugin_states
    _plugins = {}
    loaded = []
    errors = []
    config = _load_config()

    for py_file in sorted(PLUGINS_DIR.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        try:
            manifest = _load_plugin_manifest(py_file)
            if manifest is None:
                errors.append({"name": name, "error": "manifest.json not found — plugin skipped"})
                logger.warning("Plugin '%s' has no manifest.json — skipping", name)
                continue

            _plugins[name] = _build_plugin_record(name, py_file, config, manifest)
            _plugin_states[name] = _plugins[name]["enabled"]
            loaded.append(name)
            _register_plugin_in_tool_registry(name, _plugins[name])

            # P9.2-FIXUP: on_start auto-execution at load is DISABLED. Running plugin
            # subprocess code as a side effect of discovery bypasses the kernel policy
            # + approval gate. Plugin execution now happens only through the unified
            # kernel (e.g. POST /api/extra/plugins/run) after admin classification.

        except Exception as e:
            errors.append({"name": name, "error": str(e)})

    return {"ok": True, "loaded": loaded, "errors": errors, "count": len(loaded)}


def reload_plugins() -> dict:
    """Перезагружает все плагины."""
    return load_plugins()


# ═══════════════════════════════════════════════════════════════
# СПИСОК / ИНФО
# ═══════════════════════════════════════════════════════════════

def list_plugins() -> dict:
    """Список всех плагинов с метаданными."""
    items = []
    for name, info in _plugins.items():
        items.append({
            "name": name,
            "description": info["description"],
            "author": info["author"],
            "version": info["version"],
            "category": info["category"],
            "icon": info["icon"],
            "enabled": info["enabled"],
            "triggers": info["triggers"],
            "has_hooks": bool(info["hooks"]),
            "config": info["user_settings"],
            "path": info["path"],
        })
    return {"ok": True, "plugins": items, "count": len(items)}


def get_plugin_info(name: str) -> dict:
    """Подробная информация о плагине."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    info = _plugins[name]
    return {
        "ok": True,
        "name": name,
        "description": info["description"],
        "author": info["author"],
        "version": info["version"],
        "category": info["category"],
        "icon": info["icon"],
        "enabled": info["enabled"],
        "triggers": info["triggers"],
        "hooks": list(info["hooks"]),
        "config": info["user_settings"],
        "default_config": info["default_config"],
        "path": info["path"],
    }


# ═══════════════════════════════════════════════════════════════
# ВКЛЮЧЕНИЕ / ВЫКЛЮЧЕНИЕ
# ═══════════════════════════════════════════════════════════════

def enable_plugin(name: str) -> dict:
    """Включает плагин."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    _plugins[name]["enabled"] = True
    _plugin_states[name] = True
    _set_plugin_config(name, {"enabled": True})
    try:
        import app.application.tool_registry.runtime as _tr
        _tr.update_tool(name, {"enabled": True})
    except Exception as exc:
        logger.warning(f"Plugin '{name}' enable sync to tool-registry failed: {exc}")
    return {"ok": True, "name": name, "enabled": True}


def disable_plugin(name: str) -> dict:
    """Выключает плагин."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    _plugins[name]["enabled"] = False
    _plugin_states[name] = False
    _set_plugin_config(name, {"enabled": False})
    try:
        import app.application.tool_registry.runtime as _tr
        _tr.update_tool(name, {"enabled": False})
    except Exception as exc:
        logger.warning(f"Plugin '{name}' disable sync to tool-registry failed: {exc}")
    return {"ok": True, "name": name, "enabled": False}


def update_plugin_settings(name: str, settings: dict) -> dict:
    """Обновляет пользовательские настройки плагина."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    merged = {**_plugins[name]["user_settings"], **settings}
    _plugins[name]["user_settings"] = merged
    _set_plugin_config(name, {"settings": merged})
    return {"ok": True, "name": name, "settings": merged}


# ═══════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════

def run_plugin(name: str, args: dict = None) -> dict:
    """Запускает плагин по имени в дочернем процессе."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}. Доступные: {list(_plugins.keys())}"}
    info = _plugins[name]
    if not info["enabled"]:
        return {"ok": False, "error": f"Плагин {name} выключен"}
    full_args = {**info["user_settings"], **(args or {})}
    timeout = int(info.get("timeout", PLUGIN_DEFAULT_TIMEOUT))
    return _run_plugin_subprocess(info["path"], {"action": "run", "args": full_args}, timeout=timeout)


# ═══════════════════════════════════════════════════════════════
# ХУКИ — вызываются агентом автоматически
# ═══════════════════════════════════════════════════════════════

def fire_hook(hook_name: str, data: Any = None) -> list[dict]:
    """Fire a lifecycle hook in all enabled plugins that declare it.

    Only on_start, on_message, on_response are allowed. Each hook runs in a
    dedicated child subprocess with the plugin's timeout. Subprocess errors
    and timeouts are logged as controlled warnings (not silently dropped).
    Returns [{"plugin": name, "result": value}, ...] for non-None hook results.
    """
    if hook_name not in _ALLOWED_HOOKS:
        logger.warning("fire_hook: '%s' is not an allowed lifecycle hook", hook_name)
        return []
    results = []
    for name, info in _plugins.items():
        if not info["enabled"]:
            continue
        if hook_name not in info["hooks"]:
            continue
        timeout = int(info.get("timeout", PLUGIN_DEFAULT_TIMEOUT))
        try:
            proc_result = _run_plugin_subprocess(
                info["path"],
                {"action": "hook", "hook_name": hook_name, "data": data},
                timeout=timeout,
            )
            if not proc_result.get("ok"):
                logger.warning(
                    "Plugin '%s' hook '%s' failed: %s",
                    name, hook_name, proc_result.get("error"),
                )
                continue
            hook_value = proc_result.get("result")
            if hook_value:
                results.append({"plugin": name, "result": hook_value})
        except Exception as exc:
            logger.warning("Plugin '%s' hook '%s' unexpected error: %s", name, hook_name, exc)
    return results


def check_triggers(user_text: str) -> list[dict]:
    """Проверяет триггеры всех активных плагинов. Возвращает совпавшие."""
    matched = []
    lower = user_text.lower()
    for name, info in _plugins.items():
        if not info["enabled"]:
            continue
        for trigger in info.get("triggers", []):
            if trigger.lower() in lower:
                matched.append({"name": name, "trigger": trigger, "info": info})
                break
    return matched


def run_triggered(user_text: str) -> list[dict]:
    """Находит и запускает плагины по триггерам. Возвращает результаты."""
    results = []
    for match in check_triggers(user_text):
        name = match["name"]
        result = run_plugin(name, {"text": user_text, "trigger": match["trigger"]})
        results.append({"plugin": name, "trigger": match["trigger"], **result})
    return results


# ═══════════════════════════════════════════════════════════════
# СОЗДАНИЕ / ЗАГРУЗКА (авторинг из UI)
# ═══════════════════════════════════════════════════════════════

import re as _re

_NAME_RE = _re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UPLOAD_MAX_BYTES: int = 256 * 1024  # 256 KiB per file — plugins are tiny scripts

_SKELETON_TEMPLATE = '''\
"""{name} — Elira plugin (auto-generated skeleton)."""

# Metadata surfaced via `inspect` (optional, mirrored from the manifest).
CATEGORY = {category!r}
DESCRIPTION = {description!r}
TRIGGERS: list[str] = []
ICON = "🔌"
VERSION = "1.0"
AUTHOR = ""
CONFIG: dict = {{}}


def run(args: dict) -> dict:
    """Entry point. `args` is the JSON payload from the caller.

    Return a JSON-serialisable dict. This skeleton just echoes the input.
    """
    return {{"ok": True, "echo": args}}
'''


def _sanitize_plugin_stem(raw: str) -> str:
    """Validate a plugin file stem from untrusted UI input.

    Rejects empty names, leading underscore (reserved for runner internals),
    anything outside [a-z0-9_-], and path-traversal. Returns the clean stem
    or raises ValueError with a user-facing message.
    """
    stem = (raw or "").strip()
    if stem.lower().endswith(".py"):
        stem = stem[:-3]
    stem = stem.strip()
    if not stem:
        raise ValueError("Имя плагина пустое")
    if stem.startswith("_"):
        raise ValueError("Имя не может начинаться с '_' (зарезервировано)")
    if not _NAME_RE.match(stem):
        raise ValueError(
            "Имя: только строчные латинские буквы, цифры, '-' и '_', "
            "до 64 символов, начинается с буквы или цифры"
        )
    return stem


def _default_manifest(stem: str, category: str = "", description: str = "") -> dict:
    """Build a safe default manifest: disabled, sane limits, no capabilities."""
    return {
        "name": stem,
        "version": "1.0",
        "enabled": False,  # forbidden + disabled until admin classification
        "timeout": PLUGIN_DEFAULT_TIMEOUT,
        "capabilities": [],
        "category": category or "utility",
        "icon": "🔌",
        "description": description or stem,
        "author": "",
        "triggers": [],
        "hooks": [],
        "config": {},
    }


def _write_plugin_files(stem: str, py_text: str, manifest: dict) -> Path:
    """Write <stem>.py + <stem>.manifest.json as UTF-8 (no BOM). Caller ensures
    neither file pre-exists. Returns the .py path."""
    py_path = (PLUGINS_DIR / f"{stem}.py").resolve()
    manifest_path = (PLUGINS_DIR / f"{stem}.manifest.json").resolve()
    # Defence in depth: both targets must stay inside PLUGINS_DIR.
    base = PLUGINS_DIR.resolve()
    for p in (py_path, manifest_path):
        if base not in p.parents:
            raise ValueError("Недопустимый путь плагина")
    py_path.write_text(py_text, encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return py_path


def create_plugin(name: str, category: str = "", description: str = "") -> dict:
    """Generate a skeleton plugin (<name>.py + manifest, disabled) then reload.

    The .py is never imported here — discovery only reads the manifest, and the
    plugin lands forbidden + disabled until an admin classifies it.
    """
    try:
        stem = _sanitize_plugin_stem(name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if (PLUGINS_DIR / f"{stem}.py").exists():
        return {"ok": False, "error": f"Плагин '{stem}' уже существует"}

    category = (category or "").strip()
    description = (description or "").strip()
    py_text = _SKELETON_TEMPLATE.format(
        name=stem, category=category or "utility", description=description or stem
    )
    manifest = _default_manifest(stem, category, description)
    try:
        _write_plugin_files(stem, py_text, manifest)
    except Exception as exc:
        return {"ok": False, "error": f"Запись не удалась: {exc}"}

    reload_result = reload_plugins()
    return {"ok": True, "name": stem, "reload": reload_result}


def upload_plugin(
    filename: str,
    py_content: str,
    manifest_content: str | None = None,
) -> dict:
    """Save an uploaded plugin .py (+ optional manifest) then reload.

    Untrusted .py text is written to disk but never imported into the backend;
    it can only run out-of-process after admin classification. If no manifest is
    supplied, a safe disabled default is generated. The manifest's `enabled` flag
    is always forced to False on upload.
    """
    try:
        stem = _sanitize_plugin_stem(filename)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if py_content is None:
        return {"ok": False, "error": "Пустой файл плагина"}
    py_bytes = py_content.encode("utf-8")
    if len(py_bytes) == 0:
        return {"ok": False, "error": "Пустой файл плагина"}
    if len(py_bytes) > _UPLOAD_MAX_BYTES:
        return {"ok": False, "error": f"Файл больше {_UPLOAD_MAX_BYTES // 1024} КиБ"}

    if (PLUGINS_DIR / f"{stem}.py").exists():
        return {"ok": False, "error": f"Плагин '{stem}' уже существует"}

    if manifest_content:
        if len(manifest_content.encode("utf-8")) > _UPLOAD_MAX_BYTES:
            return {"ok": False, "error": "manifest больше лимита"}
        try:
            manifest = json.loads(manifest_content)
        except Exception as exc:
            return {"ok": False, "error": f"manifest не парсится: {exc}"}
        if not isinstance(manifest, dict):
            return {"ok": False, "error": "manifest должен быть объектом JSON"}
    else:
        manifest = _default_manifest(stem)

    # Never trust the uploaded manifest's enable flag — admin must classify first.
    manifest["enabled"] = False
    manifest.setdefault("name", stem)

    try:
        _write_plugin_files(stem, py_content, manifest)
    except Exception as exc:
        return {"ok": False, "error": f"Запись не удалась: {exc}"}

    reload_result = reload_plugins()
    return {"ok": True, "name": stem, "reload": reload_result}


# ═══════════════════════════════════════════════════════════════
# АВТОЗАГРУЗКА
# ═══════════════════════════════════════════════════════════════

load_plugins()
