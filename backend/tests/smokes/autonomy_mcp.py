"""Live MCP creation/repair/reuse acceptance. `prepare` never calls a model.

Run from the repository with backend/.venv/Scripts/python.exe. Each phase uses
a fresh Python process and ordinary stream_code_agent, without injected tools.
The agent writes its own integration; the fixture implements HTTP only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import urlopen

from autonomy_mcp import acceptance_oracle as oracle
from autonomy_mcp.warehouse_fixture import PAGE_SIZE, make_truth, serve

PLATFORM = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = PLATFORM / ".scratch/autonomy-mcp-acceptance"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def source_files() -> list[Path]:
    return [Path(__file__).resolve(), *sorted(Path(__file__).with_suffix("").glob("*.py"))]


def frozen_hashes(root: Path, code_root: Path) -> dict:
    return {"harness": {path.name: oracle.file_hash(path) for path in source_files()},
            "oracle": {path.name: oracle.file_hash(path) for path in sorted((root / "control").glob("truth-*.json"))},
            "application": oracle.tree_hashes(code_root / "backend/app"),
            "installed_skills": oracle.tree_hashes(code_root / "skills"),
            "kernel": oracle.file_hash(PLATFORM / "scripts/elira_release.py")}


def production_snapshot() -> dict:
    """Read-only digests; no application imports, credentials or row values logged."""
    data = PLATFORM / "data"
    result = {"skill_source": oracle.tree_hashes(data / "skill_development")}
    site_packages = PLATFORM / "backend/.venv/Lib/site-packages"
    result["python_distributions"] = {
        path.relative_to(site_packages).as_posix(): oracle.file_hash(path)
        for pattern in ("*.dist-info/METADATA", "*.dist-info/RECORD")
        for path in sorted(site_packages.glob(pattern))}
    for name in ("mcp_servers.json", "lsp_servers.json", "settings.json"):
        path = data / name
        result[name] = oracle.file_hash(path) if path.is_file() else None
    database = data / "code_agent_sessions.db"
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=20) as connection:
            digest = hashlib.sha256()
            for line in connection.iterdump():
                digest.update(line.encode("utf-8") + b"\n")
            result[database.name] = digest.hexdigest()
    return result


def prompt(root: Path, manifest: dict, phase: int) -> str:
    common = (
        f"Демонстрационный склад: {manifest['base_url']}, описание HTTP API: {manifest['base_url']}/docs. "
        f"Рабочая папка: {root / 'project'}. Получи все позиции со всех страниц и составь два CSV "
        f"output/stock_{phase:02}.csv (sku,name,quantity,minimum) и output/shortages_{phase:02}.csv "
        "(sku,quantity,minimum,shortfall), где во второй файл попадают только позиции с положительным дефицитом. "
        "Количества в обычных единицах, без округления; дефицит = max(minimum−quantity,0). "
        "Заголовки ровно как указаны, строки по возрастанию SKU, UTF-8 без BOM, LF. "
        "Проверь полноту и арифметику результата. "
    )
    if phase == 1:
        return (
            "Подключи этот склад как повторно используемую MCP-интеграцию Elira и сделай первый отчёт. "
            "Сначала проверь уже подключённые возможности и изучи готовые решения, официальный репозиторий "
            "и документацию MCP/Python SDK в интернете: https://github.com/modelcontextprotocol/python-sdk "
            "и https://modelcontextprotocol.io/docs/develop/build-server. Выбери, что можно использовать "
            "или адаптировать, затем создай только недостающее. Для совместимости: здешний клиент "
            "объявляет MCP 2024-11-05; официальный SDK v1.12.4 поддерживает этот протокол. "
            "Рабочие данные отчёта должны поступить через подключённый MCP-инструмент. "
            "Сохрани интеграцию и инструкцию как проверенный повторно используемый пакет с локальной Git-историей, "
            "чтобы она находилась и работала в новом чате после перезапуска. В SOURCES.md сохрани реальные URL, "
            "использованную версию/commit, лицензию и что адаптировано. " + common
        )
    if phase == 2:
        return (
            "Сделай новый отчёт по этому складу через уже сохранённую MCP-интеграцию. "
            "Сначала попробуй её текущую версию как есть. Если она не работает, разбери реальный ответ сервиса, "
            "исправь эту же интеграцию, проверь, сохрани следующую версию и повтори получение данных через MCP. "
            "Отдельную заменяющую интеграцию создавать не нужно. " + common
        )
    return (
        "Сделай следующий отчёт по этому складу с помощью ранее сохранённой MCP-интеграции. "
        "Это новый чат после перезапуска. Найди и используй готовую возможность; HTTP API остался прежним, "
        "изменились только складские данные. " + common
    )


def prepare(root: Path, code_root: Path) -> int:
    manifest_path = root / "control/manifest.json"
    if manifest_path.exists():
        print(json.dumps({"prepared": True, "manifest": str(manifest_path), "unchanged": True}))
        return 0
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Refusing to initialize a nonempty unrecognized acceptance directory")
    for relative in ("control", "project/output", "data", "runs", "config", "temp", "pip-cache", "uv-cache", "uv-python"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "root": str(root),
                "code_root": str(code_root), "port": port, "base_url": f"http://127.0.0.1:{port}",
                "page_size": PAGE_SIZE, "phases": [1, 2, 3], "agent_writes_mcp": True,
                "permission_mode": "bypass", "thinking": False, "skill_advisor_mode": "shadow"}
    truth = {phase: make_truth(phase) for phase in (1, 2, 3)}
    for phase, rows in truth.items():
        oracle.write_json(root / "control" / f"truth-{phase}.json", rows)
        (root / "control" / f"prompt-{phase}.txt").write_text(prompt(root, manifest, phase) + "\n", encoding="utf-8", newline="\n")
    # One offline self-check: both encodings and every page match integer truth.
    observed = {}
    with serve(port, truth, root / "control/fixture-selfcheck.jsonl") as server:
        for phase, rows in truth.items():
            server.phase = phase
            cursor, recovered = (1 if phase == 1 else None), []
            while True:
                route = f"/v1/stock?page={cursor}" if phase == 1 else "/v2/inventory" + (f"?cursor={cursor}" if cursor else "")
                with urlopen(server.base_url + route, timeout=5) as response:
                    page = json.load(response)
                if phase == 1:
                    from decimal import Decimal
                    recovered.extend({"sku": row["sku"], "name": row["name"],
                                      "quantity_milli": int(Decimal(row["quantity"]) * 1000),
                                      "minimum_milli": int(Decimal(row["minimum"]) * 1000)} for row in page["items"])
                    cursor = page["next_page"]
                else:
                    recovered.extend({"sku": row["code"], "name": row["label"],
                                      "quantity_milli": row["available_milliunits"],
                                      "minimum_milli": row["minimum_milliunits"]} for row in page["records"])
                    cursor = page["next_cursor"]
                if cursor is None:
                    break
            if recovered != rows:
                raise AssertionError(f"Fixture self-check failed for phase {phase}")
            observed[str(phase)] = {"rows": len(rows), "pages": (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE}
    manifest["fixture_selfcheck"] = observed
    # Exercise the external oracle independently: correct CSV passes; omission
    # of the tail row and a thousandfold unit error must both fail.
    import csv
    import tempfile
    from decimal import Decimal
    with tempfile.TemporaryDirectory(prefix="oracle-check-", dir=root / "temp") as temporary:
        sample = Path(temporary) / "stock.csv"
        rows = sorted(truth[1], key=lambda row: row["sku"])

        def write_sample(values: list[dict], scale: int = 1000) -> None:
            with sample.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(["sku", "name", "quantity", "minimum"])
                for row in values:
                    writer.writerow([row["sku"], row["name"], Decimal(row["quantity_milli"]) / scale,
                                     Decimal(row["minimum_milli"]) / 1000])

        write_sample(rows)
        if not oracle.verify_csv(sample, truth[1], False)["ok"]:
            raise AssertionError("Oracle rejected the correct independent CSV")
        write_sample(rows[:-1])
        if oracle.verify_csv(sample, truth[1], False)["ok"]:
            raise AssertionError("Oracle accepted an omitted last page item")
        write_sample(rows, scale=1)
        if oracle.verify_csv(sample, truth[1], False)["ok"]:
            raise AssertionError("Oracle accepted wrong units")
    manifest["oracle_selfcheck"] = {"correct_accepted": True, "omission_rejected": True, "wrong_units_rejected": True}
    manifest["truth_hashes"] = {str(phase): oracle.file_hash(root / "control" / f"truth-{phase}.json") for phase in truth}
    oracle.write_json(manifest_path, manifest)
    (root / "project/README.md").write_text(
        "# Демонстрационный склад\n\nЭто отдельная рабочая область приёмки. "
        f"HTTP API: {manifest['base_url']}/docs. Сервис не является MCP. "
        "Исполняемые зависимости интеграции устанавливаются внутри её собственного пакета.\n",
        encoding="utf-8", newline="\n")
    print(json.dumps({"prepared": True, "manifest": str(manifest_path), "selfcheck": observed,
                      "model_calls": 0}, ensure_ascii=False))
    return 0


def worker(root: Path, phase: int, manifest: dict) -> int:
    """The only path importing agent code or contacting the inference server."""
    code_root = Path(manifest["code_root"])
    sys.path.insert(0, str(code_root / "backend"))
    from dotenv import load_dotenv
    load_dotenv(PLATFORM / "backend/.env.local", override=False)
    os.environ.update({"ELIRA_DATA_DIR": str(root / "data"), "ELIRA_AGENT_RUNS_DIR": str(root / "runs"),
                       "ELIRA_PLATFORM_ROOT": str(PLATFORM), "ELIRA_CONFIG_ROOT": str(root / "config"),
                       "ELIRA_RELEASE_ID": "isolated-mcp-acceptance", "LOCAL_EMBED_ENABLED": "false",
                       "ELIRA_SKILL_ADVISOR_MODE": "shadow", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "PIP_CACHE_DIR": str(root / "pip-cache"),
                       "UV_CACHE_DIR": str(root / "uv-cache"), "UV_PYTHON_INSTALL_DIR": str(root / "uv-python"),
                       "GIT_CEILING_DIRECTORIES": str(root),
                       "TEMP": str(root / "temp"), "TMP": str(root / "temp"), "TMPDIR": str(root / "temp")})
    sys.dont_write_bytecode = True
    from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
    from app.application.code_agent import skill_development
    from app.application.code_agent.tools import _run, _shell
    from app.application.tool_providers import lsp_runtime, mcp_runtime
    from app.core.config import DATA_DIR, ROOT_DIR
    from app.infrastructure.llm.openai_compatible import local_llm_config
    if DATA_DIR != root / "data" or ROOT_DIR != code_root or mcp_runtime.CONFIG_PATH != root / "data/mcp_servers.json":
        raise RuntimeError("Agent state isolation assertion failed")
    if skill_development.ROOT != root / "data/skill_development":
        raise RuntimeError("Skill state isolation assertion failed")
    cfg = local_llm_config()
    if not cfg.enabled:
        raise RuntimeError("Local inference is not configured")
    run_id = f"mcp-acceptance-{phase}-{time.time_ns()}"
    summary_path = root / f"phase-{phase}.json"
    info = {"phase": phase, "pid": os.getpid(), "run_id": run_id, "session_id": f"mcp-new-chat-{phase}",
            "started_utc": datetime.now(timezone.utc).isoformat(), "model": cfg.model,
            "base_url": cfg.base_url, "context_window": cfg.context_window, "history_messages": 0,
            "thinking": False, "permission_mode": "bypass", "skill_advisor_mode": "shadow",
            "before": oracle.snapshot(root), "mcp_before": [{"id": item["id"], "status": item["status"]}
                                                            for item in mcp_runtime.list_servers()],
            "prompt": prompt(root, manifest, phase), "cleanup_errors": [], "source_first_seen": {}}
    instructions = (
        f"Это автономная приёмка на вымышленных данных. Рабочая область: {root / 'project'}. "
        f"Данные и пакеты этого запуска изолированы в {root / 'data'}. "
        "Меняй только эту рабочую область и изолированные пакеты, не действующее приложение, "
        "не глобальные зависимости и не другие проекты. Эталон и служебные файлы стенда вне рабочей области "
        "не относятся к задаче. Интернет для поиска и чтения документации разрешён. "
        "Выполни задачу самостоятельно без уточняющих вопросов."
    )
    info["task_instructions"] = instructions
    oracle.write_json(summary_path, info)
    stopped = threading.Event()

    def cancellation_monitor() -> None:
        while not stopped.wait(0.5):
            if (root / "STOP").exists() and request_cancel(run_id):
                info["cancel_reason"] = "operator_stop"
                return

    thread = threading.Thread(target=cancellation_monitor, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with (root / f"events-{phase}.jsonl").open("w", encoding="utf-8", newline="\n") as output:
            for event in stream_code_agent(user_message=info["prompt"], task_instructions=instructions,
                    project_root=root / "project", working_dir=root / "project", model=cfg.model,
                    run_id=run_id, session_id=info["session_id"], conversation_history=[],
                    auto_remember=False, permission_mode="bypass", thinking=False):
                observed = {**event, "observed_ns": time.time_ns()}
                output.write(json.dumps(observed, ensure_ascii=False, default=str) + "\n")
                output.flush()
                kind = event.get("type")
                if kind == "tool_call":
                    for base in (root / "project", root / "data/skill_development/packages"):
                        for relative in oracle.tree_hashes(base):
                            if Path(relative).suffix in {".py", ".js", ".ts", ".mjs", ".cjs", ".rs"}:
                                info["source_first_seen"].setdefault(str(base / relative), observed["observed_ns"])
                    print(json.dumps({"phase": phase, "step": event.get("step"), "tool": event.get("tool"),
                                      "operation": event.get("arguments", {}).get("operation"),
                                      "ok": event.get("ok"), "result": str(event.get("result", ""))[:350]},
                                     ensure_ascii=False), flush=True)
                elif kind in {"done", "error", "final_response", "workflow_request"}:
                    info[kind] = observed
                    print(json.dumps(observed, ensure_ascii=False, default=str)[:1400], flush=True)
                if kind in {"tool_call", "done", "error", "final_response"}:
                    oracle.write_json(summary_path, info)
    except Exception as exc:
        info.update(exception=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        print(info["traceback"], flush=True)
    finally:
        stopped.set()
        thread.join(timeout=2)
        info["elapsed_seconds"] = round(time.monotonic() - started, 3)
        info["mcp_before_cleanup"] = [{"id": item["id"], "status": item["status"]} for item in mcp_runtime.list_servers()]
        for label, cleanup in (("mcp", mcp_runtime.stop_all_servers), ("lsp", lsp_runtime.stop_all_servers),
                               ("server", _run.stop_all_servers), ("run_processes", lambda: _shell.kill_run_processes(run_id))):
            try:
                cleanup()
            except Exception as exc:
                info["cleanup_errors"].append({"runtime": label, "error": str(exc)})
        info["after"] = oracle.snapshot(root)
        info["validated_packages"] = {}
        for name in info["after"]["active"]:
            try:
                info["validated_packages"][name] = {"ok": True, "package": skill_development.active_package(name)}
            except Exception as exc:
                info["validated_packages"][name] = {"ok": False, "error": str(exc)}
        info["finished_utc"] = datetime.now(timezone.utc).isoformat()
        oracle.write_json(summary_path, info)
    return 1 if info.get("exception") else 0


def run_phase(root: Path, phase: int, manifest: dict, server) -> bool:
    import psutil
    summary_path = root / f"phase-{phase}.json"
    if summary_path.exists() or (root / f"events-{phase}.jsonl").exists():
        raise RuntimeError("An observed phase must never be overwritten")
    previous = oracle.read_json(root / f"phase-{phase - 1}.json") if phase > 1 else None
    if phase > 1 and (not previous or not previous.get("verification", {}).get("ok")):
        raise RuntimeError("Previous phase has not passed; preserve evidence and review the failure first")
    code_root = Path(manifest["code_root"])
    freeze_path = root / "control/frozen-inputs.json"
    frozen = frozen_hashes(root, code_root)
    if phase == 1:
        oracle.write_json(freeze_path, frozen)
    elif oracle.read_json(freeze_path) != frozen:
        raise RuntimeError("Application or oracle changed between phases")
    if {str(n): oracle.file_hash(root / "control" / f"truth-{n}.json") for n in (1, 2, 3)} != manifest["truth_hashes"]:
        raise RuntimeError("Frozen expected results changed")
    protection = production_snapshot()
    server.phase = phase
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    tracked = {}
    with (root / f"worker-{phase}.log").open("wb") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--root", str(root),
                                  "--phase", str(phase)], stdout=log, stderr=subprocess.STDOUT, env=env,
                                 cwd=root / "project", creationflags=CREATE_NO_WINDOW)
        process = psutil.Process(child.pid)
        process_identity = {"pid": child.pid, "created": process.create_time()}
        while child.poll() is None:
            try:
                for descendant in process.children(recursive=True):
                    tracked[(descendant.pid, descendant.create_time())] = descendant
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(0.25)
    survivors = []
    for (pid, created), descendant in tracked.items():
        try:
            if descendant.is_running() and descendant.create_time() == created:
                survivors.append({"pid": pid, "created": created, "name": descendant.name()})
                # Exact descendants only; never kill by name or a reused PID.
                descendant.terminate()
                try:
                    descendant.wait(timeout=5)
                except psutil.TimeoutExpired:
                    if descendant.is_running() and descendant.create_time() == created:
                        descendant.kill()
                        descendant.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            survivors.append({"pid": pid, "created": created, "cleanup_error": str(exc)})
    summary = oracle.read_json(summary_path, {"phase": phase, "exception": "worker failed before initialization",
                                             "before": oracle.snapshot(root), "after": oracle.snapshot(root)})
    summary.update(worker_exit_code=child.returncode, process_identity=process_identity, surviving_processes=survivors,
                   tracked_processes=[{"pid": pid, "created": created} for pid, created in tracked],
                   frozen_inputs_unchanged=frozen_hashes(root, code_root) == frozen,
                   production_unchanged=production_snapshot() == protection)
    oracle.write_json(root / f"protected-before-{phase}.json", protection)
    oracle.write_json(root / f"protected-after-{phase}.json", production_snapshot())
    try:
        summary["verification"] = oracle.verify(root, phase, manifest, summary, previous)
    except Exception as exc:
        summary["verification"] = {"ok": False, "oracle_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    oracle.write_json(summary_path, summary)
    print(json.dumps({"phase": phase, "verification": summary["verification"], "elapsed_seconds": summary.get("elapsed_seconds")},
                     ensure_ascii=False), flush=True)
    return summary["verification"]["ok"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "worker"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--code-root", type=Path, default=PLATFORM)
    parser.add_argument("--phase", choices=("1", "2", "3", "all"), default="all")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(PLATFORM / ".scratch"):
        raise RuntimeError("Acceptance state must be under this repository's .scratch directory")
    if args.command == "prepare":
        return prepare(root, args.code_root.resolve())
    manifest = oracle.read_json(root / "control/manifest.json")
    if not manifest or manifest["root"] != str(root):
        raise RuntimeError("Run prepare first in this exact directory")
    if args.command == "worker":
        if args.phase == "all":
            raise ValueError("Worker requires one phase")
        return worker(root, int(args.phase), manifest)
    if (root / "STOP").exists():
        raise RuntimeError("Operator STOP is present; review it before beginning a phase")
    truth = {phase: oracle.read_json(root / "control" / f"truth-{phase}.json") for phase in (1, 2, 3)}
    with serve(manifest["port"], truth, root / "control/http.jsonl") as server:
        for phase in ((1, 2, 3) if args.phase == "all" else (int(args.phase),)):
            if not run_phase(root, phase, manifest, server):
                return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
