"""Process wrapper used only by the canonical ``run_server(kind='job')`` path."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("ERROR: durable job worker requires one spec path", flush=True)
        return 2
    try:
        spec_path = Path(args[0])
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        job_id = str(spec["job_id"])
        command = str(spec["command"])
        cwd = str(spec["cwd"])
        launch_path = Path(str(spec["launch_path"]))
        result_path = Path(str(spec["result_path"]))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"ERROR: invalid durable job spec: {exc}", flush=True)
        return 2
    # Publish the worker PID + OS creation identity before launching the shell
    # command. The backend journal already contains a ``starting`` record, so a
    # backend crash between Popen() and activation can be reconciled safely.
    backend_root = Path(__file__).resolve().parents[4]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        from app.application.code_agent.tools._background_jobs import process_identity

        identity = process_identity(os.getpid())
        if identity is None:
            raise RuntimeError("could not read worker process identity")
        _atomic_json(
            launch_path,
            {
                "schema_version": 1,
                "job_id": job_id,
                "pid": os.getpid(),
                "process_identity": identity,
                "launched_at": time.time(),
            },
        )
    except Exception as exc:  # noqa: BLE001 - launch must fail closed
        print(f"ERROR: durable job launch handshake failed: {exc}", flush=True)
        return 2
    finally:
        try:
            spec_path.unlink(missing_ok=True)
        except OSError:
            pass

    exit_code: int | None = None
    error: str | None = None
    try:
        child = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
        )
        exit_code = int(child.wait())
    except Exception as exc:  # noqa: BLE001 - the sidecar must survive launch failures
        error = f"job launch failed: {type(exc).__name__}: {exc}"
        print(f"ERROR: {error}", flush=True)
        exit_code = 127

    status = "completed" if exit_code == 0 else "failed"
    _atomic_json(
        result_path,
        {
            "schema_version": 1,
            "job_id": job_id,
            "status": status,
            "exit_code": exit_code,
            "finished_at": time.time(),
            "error": error,
        },
    )
    return exit_code if 0 <= exit_code <= 255 else 1


if __name__ == "__main__":
    raise SystemExit(main())
