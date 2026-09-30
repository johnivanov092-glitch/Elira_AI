"""Local browser frontend adapter for an isolated Elira installation.

All release verification, admission, draining and recovery use the installation's
unchanged release supervisor. This adapter only replaces the native UI process.
"""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


SELF = Path(__file__).resolve()
WORKSPACE = SELF.parent.parent


def config() -> dict:
    value = json.loads(SELF.with_name("runtime.json").read_text(encoding="utf-8"))
    for key in ("platform", "data", "runs", "temp"):
        path = Path(value[key]).resolve()
        if path == WORKSPACE or not path.is_relative_to(WORKSPACE):
            raise ValueError(f"Runtime {key} must be inside this installation")
    if int(value["backend_port"]) != 18581 or int(value["ui_port"]) != 18582:
        raise ValueError("Unexpected isolated installation ports")
    return value


def environment() -> dict:
    cfg = config()
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "ELIRA_RELEASE_TOKEN", "ELIRA_RELEASE_INSTANCE"):
        env.pop(key, None)
    env.update({
        "ELIRA_PLATFORM_ROOT": cfg["platform"],
        "ELIRA_CONFIG_ROOT": str(Path(cfg["platform"]) / "backend"),
        "ELIRA_DATA_DIR": cfg["data"], "ELIRA_AGENT_RUNS_DIR": cfg["runs"],
        "ELIRA_EXTERNAL_BACKEND": "1", "ELIRA_DRIFT_CHECK": "0",
        "LOCAL_EMBED_ENABLED": "false", "ELIRA_SKILL_ADVISOR_MODE": "shadow",
        "WEBVIEW2_USER_DATA_FOLDER": str(WORKSPACE / "webview2"),
        "VITE_API_BASE_URL": f"http://127.0.0.1:{cfg['backend_port']}",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CACHE_DIR": str(WORKSPACE / "pip-cache"),
        "npm_config_cache": str(WORKSPACE / "npm-cache"),
        "CARGO_HOME": str(WORKSPACE / "cargo-home"), "UV_CACHE_DIR": str(WORKSPACE / "uv-cache"),
        "GIT_CEILING_DIRECTORIES": str(WORKSPACE),
    })
    if cfg.get("rust_toolchain_bin"):
        env["PATH"] = cfg["rust_toolchain_bin"] + os.pathsep + env.get("PATH", "")
        env["RUSTC"] = str(Path(cfg["rust_toolchain_bin"]) / "rustc.exe")
    env.update(cfg["model_env"])
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = cfg["temp"]
    return env


def release_module():
    path = Path(config()["platform"]) / "scripts/elira_release.py"
    spec = importlib.util.spec_from_file_location("browser_release_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Installation release supervisor is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manager():
    cfg = config()
    os.environ.update(environment())
    module = release_module()

    class BrowserManager(module.ReleaseManager):
        def _start_ui(self, release_id: str, executable: str) -> None:
            log = (WORKSPACE / f"browser-server-{release_id}.log").open("ab")
            try:
                self.ui = subprocess.Popen(
                    [sys.executable, str(SELF), "static", "--release", release_id],
                    cwd=WORKSPACE, env=environment(), stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            finally:
                log.close()
            self._save_processes()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.ui.poll() is not None:
                    raise RuntimeError("Browser frontend server exited")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{cfg['ui_port']}/", timeout=1) as response:
                        if response.headers.get("X-Elira-Acceptance-Release") == release_id:
                            return
                except OSError:
                    pass
                time.sleep(0.1)
            raise RuntimeError("Browser frontend startup timed out")

    return BrowserManager(Path(cfg["platform"]), port=cfg["backend_port"], startup_timeout=90)


def static_server(release_id: str) -> None:
    cfg = config()
    directory = manager().path(release_id) / "frontend/dist"
    if not (directory / "index.html").is_file():
        raise ValueError("Compiled frontend is unavailable")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Elira-Acceptance-Release", release_id)
            super().end_headers()

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(("127.0.0.1", cfg["ui_port"]), Handler).serve_forever()


def serve(preview: str | None = None) -> None:
    instance = manager()
    module = release_module()
    stop = WORKSPACE / "STOP_SERVER"
    if stop.exists():
        stop.unlink()
    started = time.monotonic()
    with module._lock(instance.owned("supervisor.lock")):
        instance._reap_owned_processes()
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", instance.port)) == 0:
                raise RuntimeError("Port is owned by another listener")
        state = instance.recover()
        try:
            if preview:
                if preview == state.get("active"):
                    raise ValueError("An active release cannot be an unverified preview")
                instance.path(preview)
                instance._start_backend(preview)
                instance._start_ui(preview, "")
                instance._http("activate")
            elif state.get("active"):
                instance._launch_active(state["active"])
            elif not state.get("pending"):
                raise ValueError("No verified release has been requested")
            while not stop.exists() and time.monotonic() - started < int(config().get("max_trial_seconds", 14400)):
                if not preview:
                    instance.apply_pending()
                if instance.backend is None or instance.ui is None or instance.ui.poll() is not None:
                    break
                if instance.backend.poll() is not None:
                    raise RuntimeError("Isolated backend exited")
                time.sleep(1)
        finally:
            instance._stop_ui()
            if instance.backend is not None:
                try:
                    instance._http("drain")
                except OSError:
                    pass
            instance._stop_backend()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "static", "preview"))
    parser.add_argument("--release")
    args = parser.parse_args()
    if args.command in {"static", "preview"} and not args.release:
        parser.error("A release is required")
    if args.command == "static":
        static_server(args.release)
    else:
        serve(args.release if args.command == "preview" else None)


if __name__ == "__main__":
    main()
