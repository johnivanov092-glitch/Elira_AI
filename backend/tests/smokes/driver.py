# -*- coding: utf-8 -*-
"""SSE driver for live smokes: POST /api/code-agent/stream, consume the stream,
return a structured summary. stdlib-only (urllib), no test-framework coupling —
this is NOT collected by pytest (live runs take minutes and need a running
backend + LLM); the entry point is tests/smokes/run.py."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def run_smoke(*, backend: str, task_text: str, project_root: str, run_id: str,
              events_path: Path, timeout_s: int = 1200) -> dict[str, Any]:
    """Execute ONE smoke run and summarize it. Raises on transport errors;
    an in-run failure is reported via the summary (stop_reason/error)."""
    body = json.dumps({
        "message": task_text,
        "project_root": project_root,
        "run_id": run_id,
        "permission_mode": "bypass",
    }).encode("utf-8")
    req = urllib.request.Request(
        backend.rstrip("/") + "/api/code-agent/stream",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )

    t0 = time.time()
    events: list[dict] = []
    done: dict | None = None
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp, \
         open(events_path, "w", encoding="utf-8") as out:
        buf = b""
        while True:
            chunk = resp.read(1)
            if not chunk:
                break
            buf += chunk
            if not buf.endswith(b"\n\n"):
                continue
            for line in buf.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                out.write(json.dumps(ev, ensure_ascii=False) + "\n")
                if ev.get("type") == "done":
                    done = ev
            buf = b""

    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    crits = (done or {}).get("criteria") or []
    return {
        "run_id": run_id,
        "duration_s": round(time.time() - t0, 1),
        "stop_reason": (done or {}).get("stop_reason"),
        "error": (done or {}).get("error"),
        "completion_status": (done or {}).get("completion_status"),
        "confirmed": sum(1 for c in crits if c.get("status") == "confirmed"),
        "total_criteria": len(crits),
        "criteria": [{"status": c.get("status"), "text": (c.get("text") or "")[:70]}
                     for c in crits],
        "tool_calls": len(tool_calls),
        "auto_verifier_calls": sum(1 for e in tool_calls if e.get("auto_verifier")),
        "steps": sum(1 for e in events if e.get("type") == "step_started"),
        "server_ports": _server_ports(events),
    }


def _server_ports(events: list[dict]) -> list[int]:
    """Loopback ports any dev server announced during the run (for cleanup checks)."""
    ports: set[int] = set()
    for ev in events:
        for key in ("result", "text"):
            s = str(ev.get(key) or "")
            for tok in s.split():
                if "localhost:" in tok:
                    tail = tok.split("localhost:", 1)[1]
                    digits = ""
                    for ch in tail:
                        if ch.isdigit():
                            digits += ch
                        else:
                            break
                    if digits:
                        ports.add(int(digits))
    return sorted(p for p in ports if p != 8000)   # exclude the backend itself
