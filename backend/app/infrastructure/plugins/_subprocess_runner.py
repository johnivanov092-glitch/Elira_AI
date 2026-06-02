"""Out-of-process plugin execution entry point.

Invoked by plugin_system.py as a subprocess:
  python _subprocess_runner.py <plugin_path> <backend_root>

Payload JSON is read from stdin; result JSON is written to stdout.

Payload schema:
  {"action": "run",     "args": {...}}
  {"action": "hook",    "hook_name": "<name>", "data": <any>}
  {"action": "inspect"}

This isolates plugin code from the backend process: a crashing or
infinite-looping plugin cannot affect the main API server.
"""
import importlib.util
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: runner.py <plugin_path> <backend_root>"}))
        sys.exit(1)

    plugin_path = sys.argv[1]
    backend_root = sys.argv[2]

    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    try:
        spec = importlib.util.spec_from_file_location("elira_plugin", plugin_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin: {plugin_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Plugin import error: {exc}"}))
        sys.exit(1)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON payload: {exc}"}))
        sys.exit(1)

    action = payload.get("action", "run")

    try:
        if action == "run":
            args = payload.get("args", {})
            if not hasattr(mod, "run") or not callable(mod.run):
                result: dict = {"ok": False, "error": "Plugin has no run(args) function"}
            else:
                raw_result = mod.run(args)
                result = raw_result if isinstance(raw_result, dict) else {"ok": True, "result": raw_result}

        elif action == "hook":
            hook_name = payload.get("hook_name", "")
            hook_data = payload.get("data")
            fn = getattr(mod, hook_name, None)
            if fn is None or not callable(fn):
                result = {"ok": False, "error": f"Hook '{hook_name}' not found in plugin"}
            else:
                raw_result = fn(hook_data)
                result = raw_result if isinstance(raw_result, dict) else {"ok": True, "result": raw_result}

        elif action == "inspect":
            result = {
                "ok": True,
                "triggers": getattr(mod, "TRIGGERS", []),
                "hooks": [h for h in ("on_start", "on_message", "on_response")
                          if callable(getattr(mod, h, None))],
                "category": getattr(mod, "CATEGORY", "utility"),
                "icon": getattr(mod, "ICON", "🔌"),
                "description": getattr(mod, "DESCRIPTION", ""),
                "author": getattr(mod, "AUTHOR", ""),
                "version": getattr(mod, "VERSION", "1.0"),
                "config": getattr(mod, "CONFIG", {}),
            }

        else:
            result = {"ok": False, "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    main()
