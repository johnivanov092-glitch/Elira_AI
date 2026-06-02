"""Out-of-process plugin execution entry point.

Invoked by plugin_system.py as a subprocess:
  python _subprocess_runner.py <plugin_path> <backend_root>

Args JSON is read from stdin; result JSON is written to stdout.
This isolates plugin code from the backend process: a crashing or
infinite-looping plugin cannot take down the main API server.
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

    # Add backend root to path so plugin can import app.*
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
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON args: {exc}"}))
        sys.exit(1)

    try:
        result = mod.run(args)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    main()
