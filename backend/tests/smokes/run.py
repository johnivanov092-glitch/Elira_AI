# -*- coding: utf-8 -*-
"""Automated live smokes (R5): one command runs the canonical agent scenarios
against a LIVE backend (+ LLM) and asserts outcome, tool budget and cleanup
against baseline.json.

    python tests/smokes/run.py                    # all smokes
    python tests/smokes/run.py --only cli,form    # subset
    python tests/smokes/run.py --keep             # keep project dirs on PASS

Not collected by pytest (needs a running backend at --backend and the AI-server;
a full pass takes minutes). SSH canary SKIPs when its host is not allowlisted —
a precondition, not a failure. One retry per smoke on stop_reason=error (the
known llama.cpp tool-call-parse flake): retried-green is reported FLAKY-PASS.
Exit code: 0 = no failures (skips allowed), 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from driver import run_smoke  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _get_json(url: str, timeout: int = 10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _backend_up(backend: str) -> bool:
    try:
        with urllib.request.urlopen(backend.rstrip("/") + "/docs", timeout=10) as r:
            return r.status == 200
    except OSError:
        return False


def _ssh_host_allowed(backend: str, host: str) -> bool:
    try:
        cfg = _get_json(backend.rstrip("/") + "/api/code-agent/ssh/config")
        return bool(cfg.get("enabled")) and host in (cfg.get("allowed_hosts") or [])
    except Exception:
        return False


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def _run_servers_left(backend: str, run_id: str) -> list[dict]:
    """Live run_server registry entries still owned by `run_id` — the honest cleanup
    check (a leaked handle or a server with no parsed URL is invisible to the port
    probe alone; John's P1b review of the R5 DoD)."""
    try:
        data = _get_json(backend.rstrip("/") + f"/api/code-agent/servers?run_id={run_id}")
        return list(data.get("servers") or [])
    except Exception as exc:
        return [{"error": f"servers endpoint unreachable: {exc}"}]


def _evaluate(name: str, spec: dict, summary: dict, project_dir: Path) -> list[str]:
    """Return the list of assertion FAILURES (empty = pass)."""
    fails: list[str] = []
    if summary.get("stop_reason") != "answer":
        fails.append(f"stop_reason={summary.get('stop_reason')} (error: {str(summary.get('error'))[:120]})")
    if summary.get("completion_status") != spec["expect_completion"]:
        fails.append(f"completion={summary.get('completion_status')} (ожидалось {spec['expect_completion']})")
    if summary.get("confirmed") != spec["expect_confirmed"]:
        fails.append(f"confirmed={summary.get('confirmed')}/{summary.get('total_criteria')} "
                     f"(ожидалось {spec['expect_confirmed']})")
    if summary.get("tool_calls", 0) > spec["max_tool_calls"]:
        fails.append(f"tool_calls={summary.get('tool_calls')} > бюджета {spec['max_tool_calls']}")
    for rel in spec.get("artifacts") or []:
        if not (project_dir / rel).exists():
            fails.append(f"артефакт отсутствует: {rel}")
    if spec.get("check_ports_closed"):
        leaked = [p for p in summary.get("server_ports") or [] if _port_listening(p)]
        if leaked:
            fails.append(f"порт(ы) ещё слушают после прогона: {leaked} — сервер утёк")
        left = _run_servers_left(spec["_backend"], summary.get("run_id") or "")
        if left:
            fails.append(f"run_server registry НЕ пуст после прогона: {left} — handle утёк")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="Elira live smokes")
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="", help="comma-separated subset (cli,csv_named,form,ssh_canary)")
    ap.add_argument("--keep", action="store_true", help="keep project dirs on PASS too")
    args = ap.parse_args()

    baseline = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(baseline)
    unknown = [n for n in names if n not in baseline]
    if unknown:
        print(f"неизвестные смоки: {unknown}; доступны: {list(baseline)}")
        return 1

    if not _backend_up(args.backend):
        print(f"FAIL preflight: backend не отвечает на {args.backend} — подними uvicorn и повтори")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_root = Path(tempfile.gettempdir()) / "elira-smokes" / stamp
    out_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    any_fail = False

    for name in names:
        spec = baseline[name]
        req_host = spec.get("requires_ssh_host")
        if req_host and not _ssh_host_allowed(args.backend, req_host):
            print(f"SKIP {name}: ssh-хост {req_host} не в allowlist/выключен (прекондишн)")
            results[name] = {"status": "SKIP", "reason": f"ssh host {req_host} unavailable"}
            continue

        task_text = (HERE / spec["task"]).read_text(encoding="utf-8")
        spec["_backend"] = args.backend   # for the registry cleanup check
        verdict = "FAIL"
        summary: dict = {}
        fails: list[str] = []
        project_dir = out_root / name
        for attempt in (1, 2):
            if project_dir.exists():
                shutil.rmtree(project_dir, ignore_errors=True)
            project_dir.mkdir(parents=True, exist_ok=True)
            rid = f"smoke-{stamp}-{name}-a{attempt}"
            print(f"RUN  {name} (attempt {attempt}) → {project_dir}")
            try:
                summary = run_smoke(
                    backend=args.backend, task_text=task_text,
                    project_root=str(project_dir), run_id=rid,
                    events_path=out_root / f"events-{name}-a{attempt}.jsonl",
                )
            except Exception as exc:
                summary = {"stop_reason": "transport-error", "error": str(exc)[:200]}
            fails = _evaluate(name, spec, summary, project_dir)
            if not fails:
                verdict = "PASS" if attempt == 1 else "FLAKY-PASS"
                break
            # One retry on ANY failure: a real runtime regression is deterministic and
            # fails BOTH attempts → FAIL; a stochastic generation/infra flake usually
            # passes the retry → FLAKY-PASS (still visible in the report, not hidden).
            if attempt == 1:
                print(f"     attempt 1 failed ({'; '.join(fails)[:120]}) — retry")
                continue
            break

        results[name] = {"status": verdict, "fails": fails, **summary}
        line = (f"{verdict:10} {name}: {summary.get('completion_status')} "
                f"{summary.get('confirmed')}/{summary.get('total_criteria')} "
                f"tools={summary.get('tool_calls')} auto={summary.get('auto_verifier_calls')} "
                f"{summary.get('duration_s')}s")
        print(line)
        for f in fails:
            print(f"           ✗ {f}")
        if verdict == "FAIL":
            any_fail = True
            print(f"           события/проект сохранены: {out_root}")
        elif not args.keep:
            shutil.rmtree(project_dir, ignore_errors=True)

    (out_root / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИтог: {sum(1 for r in results.values() if r['status'].endswith('PASS'))} pass, "
          f"{sum(1 for r in results.values() if r['status'] == 'FAIL')} fail, "
          f"{sum(1 for r in results.values() if r['status'] == 'SKIP')} skip "
          f"→ {out_root / 'results.json'}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
