"""Opt-in unattended Qwen UI/release trial; collect evidence for later review.

Uses the existing acceptance harness and a fresh disposable installation.
No Windows policy/service changes. Full administrator mode is intentional.
This is browser UI acceptance, not Foundation containment or native Tauri QA.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid


REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / ".scratch/autonomy-ui-acceptance"
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def require_admin():
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Run Elira_Autonomy_Test.bat as administrator; Windows policy is not changed.")


def load_harness():
    source = REPO / "backend/tests/smokes/autonomy_ui.py"
    spec = importlib.util.spec_from_file_location("unattended_ui_harness", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PHASE_SECONDS = 3600
    checks_spec = importlib.util.spec_from_file_location(
        "unattended_checkpoints", REPO / ".scratch/autonomy-compaction-acceptance/checkpoints.py")
    checks = importlib.util.module_from_spec(checks_spec)
    checks_spec.loader.exec_module(checks)
    module.budget = lambda root: checks.stable_budget(root, module.MAX_BYTES, module.MIN_FREE)
    original_snapshot = module.production_snapshot
    def snapshot():
        result = original_snapshot()
        result["databases"] = {name: value["sha256"] for name, value in checks.all_databases(REPO / "data").items()}
        return result
    module.production_snapshot = snapshot
    return module


def run(root):
    require_admin()
    if root.parent != REPO / ".scratch" or not root.name.startswith("autonomy-admin-"):
        raise ValueError("Expected a fresh owned trial directory")
    if (root / "config.json").exists():
        raise ValueError("An existing trial is never restarted or overwritten")
    m = load_harness()
    report = {"status": "RUNNING", "started_at": time.time(), "root": str(root),
              "privilege_mode": "full_administrator_user_decision", "foundation": "NOT_RUN",
              "native_tauri": "NOT_RUN", "repair": "NOT_OBSERVED", "steps": []}
    server = None

    def step(name, value):
        report["steps"].append({"name": name, "at": time.time(), "result": value})
        if name in {"create", "repair", "reuse"} and not value.get("ok"):
            report.setdefault("failed_phases", []).append(name)
        save(root / "RESULT.json", report)

    def settled():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("Test supervisor exited; no assisted restart is performed")
            try:
                state = m.manager_for(root).state()
                health = m.api(root, "/health")
                if (state.get("active") and not state.get("pending") and not state.get("transition")
                        and health.get("release_id") == state["active"] and health.get("admitted") is True
                        and not health.get("draining") and health.get("active_agent_runs") == 0
                        and health.get("active_requests") == 0):
                    return state
            except OSError:
                pass
            time.sleep(1)
        raise TimeoutError("Release did not become active/admitted after the model turn")

    def browser(batch, label):
        with (root / (label + "-browser.log")).open("xb") as output:
            result = subprocess.run([shutil.which("node"), str(root / "browser_check.cjs"), str(batch), "download", label],
                                    cwd=root, stdout=output, stderr=subprocess.STDOUT, timeout=120, creationflags=HIDDEN)
        evidence = m.read_json(root / "browser" / label / "result.json")
        if evidence.get("download"):
            evidence["archive_check"] = m.oracle(root, Path(evidence["download"]), batch)
        evidence["checked_ok"] = bool(result.returncode == 0 and evidence.get("ok")
                                      and evidence.get("archive_check", {}).get("ok"))
        errors = evidence.get("archive_check", {}).get("errors", [])
        evidence["review_only"] = bool(result.returncode == 0 and evidence.get("ok") and errors
            and all(error.startswith("Author label needs independent review:") for error in errors))
        step(label, evidence)
        return evidence

    try:
        for path in (TEMPLATES / "runtime/browser_runtime.py", TEMPLATES / "browser_check.cjs"):
            if not path.is_file():
                raise FileNotFoundError(f"Existing acceptance helper is missing: {path}")
        if not shutil.which("node"):
            raise RuntimeError("Node is not available")
        step("setup", m.setup(root))
        cfg = m.config(root)
        original_cfg = m.read_json(TEMPLATES / "config.json")
        for key in ("browser_executable", "rust_toolchain_bin"):
            if original_cfg.get(key):
                cfg[key] = original_cfg[key]
        if not Path(cfg.get("browser_executable", "")).is_file():
            raise RuntimeError("Configured acceptance browser is missing")
        cfg["max_seconds_per_phase"] = m.PHASE_SECONDS
        save(root / "config.json", cfg)
        platform = Path(cfg["platform"])
        module = m.release_module(REPO)
        # Tracked source only: never copy the live checkout's .env or credentials.
        frozen_source = root / "source"
        m.command(["git", "clone", "--local", "--no-hardlinks", str(REPO), str(frozen_source)], cwd=root)
        m.command(["git", "remote", "remove", "origin"], cwd=frozen_source)
        m.copy_source(module, frozen_source, platform)
        m.copy_source(module, frozen_source, platform / ".runtime/releases/candidates" / cfg["baseline"])
        (platform / "backend/.env.local").write_text(
            "\n".join(f"{key}={value}" for key, value in cfg["model_env"].items())
            + "\nLOCAL_EMBED_ENABLED=false\n", encoding="utf-8", newline="\n")
        runtime = root / "runtime/browser_runtime.py"
        runtime.parent.mkdir()
        template = (TEMPLATES / "runtime/browser_runtime.py").read_text(encoding="utf-8")
        runtime.write_text(template.replace("started < 7200", "started < 14400"), encoding="utf-8", newline="\n")
        save(runtime.with_name("runtime.json"), cfg)
        shutil.copy2(TEMPLATES / "browser_check.cjs", root / "browser_check.cjs")
        os.environ.update(m.environment(root))
        # Reuse local build caches; no models are downloaded or selected here.
        old_cargo = TEMPLATES / "cargo-home"
        if old_cargo.is_dir():
            cfg["model_env"]["CARGO_HOME"] = str(old_cargo)
            save(root / "config.json", cfg)
            save(runtime.with_name("runtime.json"), cfg)
            os.environ.update(m.environment(root))
        step("source", {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                        "runtime_sha256": m.digest(runtime), "harness_sha256": m.digest(m.SELF)})
        m.command([sys.executable, str(m.SELF), "verify", "--root", str(root), "--release", cfg["baseline"]],
                  cwd=root, env=m.environment(root), timeout=m.VERIFY_SECONDS + 60, log=root / "baseline-build.log")
        step("baseline_request", m.manager_for(root).request(cfg["baseline"]))
        with (root / "supervisor.log").open("xb") as output:
            server = subprocess.Popen([sys.executable, str(runtime), "serve"], cwd=root, env=m.environment(root),
                                      stdout=output, stderr=subprocess.STDOUT, creationflags=HIDDEN)
        step("baseline_active", settled())
        step("create", m.phase(root, "create", 1, "user-approved-admin-unattended", attempt="run"))
        step("after_create", settled())
        checked = browser(1, "create")
        if not checked["checked_ok"] and not checked["review_only"]:
            current = m.manager_for(root)
            candidate = str(current.path(current.state()["active"]))
            m.write_json(root / "injection.json", {"candidate": candidate, "kind": "observed_failure_no_injection"})
            normal_prompt = m.task_prompt
            failure = {key: checked.get(key) for key in ("error", "downloadError", "failures", "requests")}
            failure["archive_errors"] = checked.get("archive_check", {}).get("errors", [])
            def repair_prompt(workspace, phase, batch, *, attempt=""):
                if phase != "repair":
                    return normal_prompt(workspace, phase, batch, attempt=attempt)
                return (f"В тестовой Elira {cfg['platform']} после обновления не прошла загрузка архива папки диалогов. "
                        f"Наблюдение интерфейса: {json.dumps(failure, ensure_ascii=False)}. "
                        "Исправь причину, проверь полный ZIP и установи исправление штатным механизмом релиза. "
                        "Работай самостоятельно в этой тестовой копии, исходные чаты сохрани. "
                        "После request заверши текущую задачу, чтобы supervisor смог применить обновление.")
            m.task_prompt = repair_prompt
            report["repair"] = "ATTEMPTED_ON_OBSERVED_FAILURE"
            step("repair", m.phase(root, "repair", 1, "user-approved-admin-unattended", attempt="run"))
            step("after_repair", settled())
            checked = browser(1, "repair")
        if not checked["checked_ok"] and not checked["review_only"]:
            raise RuntimeError("Archive still failed after one repair; evidence retained for review")
        step("freeze", m.freeze_feature(root, "run"))
        active = m.manager_for(root).state()["active"]
        step("new_inputs", m.seed_heldout(root, active, 3, "run"))
        step("reuse", m.phase(root, "reuse", 3, "user-approved-admin-unattended", attempt="run"))
        step("after_reuse", settled())
        reused = browser(3, "reuse")
        if not reused["checked_ok"] and not reused["review_only"]:
            raise RuntimeError("Reuse on fresh input failed")
        report["status"] = "FAILED_REVIEW_REQUIRED" if report.get("failed_phases") else "REVIEW_REQUIRED"
    except BaseException as exc:
        report.update(status="FAILED_REVIEW_REQUIRED", error=str(exc), traceback=traceback.format_exc())
    finally:
        if server is not None:
            (root / "STOP_SERVER").write_text("unattended trial complete\n", encoding="utf-8", newline="\n")
            try:
                server.wait(timeout=60)
                report["supervisor_exit"] = server.returncode
            except subprocess.TimeoutExpired:
                report["cleanup"] = "PENDING: supervisor did not stop; no forced termination"
                report["status"] = "FAILED_REVIEW_REQUIRED"
        if (root / "production-before.json").exists():
            try:
                integrity = m.integrity(root)
                step("production_integrity", integrity)
                if not integrity.get("ok"):
                    report["status"] = "FAILED_REVIEW_REQUIRED"
            except Exception as exc:
                report["integrity_error"] = str(exc)
                report["status"] = "FAILED_REVIEW_REQUIRED"
        report["finished_at"] = time.time()
        save(root / "RESULT.json", report)
    return 0 if report["status"] == "REVIEW_REQUIRED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="Internal child: fresh evidence root")
    args = parser.parse_args()
    if args.run:
        return run(args.run.resolve())
    require_admin()
    root = REPO / ".scratch" / ("autonomy-admin-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    with (root / "controller.log").open("xb") as output:
        child = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--run", str(root)],
                                 cwd=REPO, stdout=output, stderr=subprocess.STDOUT,
                                 creationflags=HIDDEN | getattr(subprocess, "DETACHED_PROCESS", 0))
    result = {"pid": child.pid, "root": str(root), "result": str(root / "RESULT.json")}
    save(REPO / ".scratch/autonomy-admin-latest.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
