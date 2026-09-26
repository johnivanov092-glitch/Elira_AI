"""External acceptance oracle; never imported by or supplied to the agent."""
from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

EXCLUDED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8", newline="\n")


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): file_hash(path)
            for path in sorted(root.rglob("*")) if path.is_file()
            and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts)}


def git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repository), *args], capture_output=True,
                            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def snapshot(root: Path) -> dict:
    development = root / "data/skill_development"
    active = read_json(development / "active.json", {})
    packages = {}
    for name, entry in active.items():
        candidate = entry["candidate_id"]
        directory = development / "packages" / name / candidate
        repository = development / "history" / name
        receipt = read_json(development / "receipts" / f"{candidate}.json", {})
        package = {"directory": str(directory), "active": entry, "receipt": receipt,
                   "files": tree_hashes(directory)}
        try:
            revision = entry["revision"]
            paths = git(repository, "ls-tree", "-r", "--name-only", "-z", revision, "--", "package").split(b"\0")
            git_files = {path.decode("utf-8")[len("package/"):]: hashlib.sha256(
                git(repository, "show", f"{revision}:{path.decode('utf-8')}")).hexdigest()
                for path in paths if path}
            package.update(git_files=git_files, git_matches=git_files == package["files"],
                           git_remote=git(repository, "remote").decode("utf-8").strip(),
                           git_status=git(repository, "status", "--porcelain").decode("utf-8").strip())
        except (OSError, RuntimeError, KeyError, subprocess.TimeoutExpired) as exc:
            package["git_error"] = str(exc)
        source_notes = directory / "SOURCES.md"
        package["sources"] = source_notes.read_text(encoding="utf-8") if source_notes.exists() else ""
        packages[name] = package
    return {"active": active, "packages": packages,
            "mcp_config": read_json(root / "data/mcp_servers.json", {"servers": []}),
            "project_files": tree_hashes(root / "project")}


def verify_csv(path: Path, truth: list[dict], shortages: bool) -> dict:
    headers = ["sku", "quantity", "minimum", "shortfall"] if shortages else ["sku", "name", "quantity", "minimum"]
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
            raise ValueError("CSV must be UTF-8 without BOM and use LF")
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        if reader.fieldnames != headers:
            raise ValueError(f"Wrong CSV headers: {reader.fieldnames!r}")
        actual = list(reader)
        expected = sorted((row for row in truth if not shortages or row["quantity_milli"] < row["minimum_milli"]),
                          key=lambda row: row["sku"])
        if len(actual) != len(expected) or [row["sku"] for row in actual] != [row["sku"] for row in expected]:
            raise ValueError("Missing, duplicate, unsorted or unexpected SKU")
        for got, want in zip(actual, expected):
            if None in got or (not shortages and got["name"] != want["name"]):
                raise ValueError(f"Invalid fields/name for {want['sku']}")
            amounts = {"quantity": want["quantity_milli"], "minimum": want["minimum_milli"]}
            if shortages:
                amounts["shortfall"] = max(want["minimum_milli"] - want["quantity_milli"], 0)
            for field, milli in amounts.items():
                value = Decimal(got[field])
                if not value.is_finite() or value != Decimal(milli) / 1000:
                    raise ValueError(f"Wrong {field} for {want['sku']}: {got[field]!r}")
        return {"ok": True, "rows": len(actual), "sha256": hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, ArithmeticError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def primary_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and (
        parsed.netloc == "modelcontextprotocol.io"
        or (parsed.netloc in {"github.com", "raw.githubusercontent.com"}
            and parsed.path.startswith("/modelcontextprotocol/"))))


def tool_windows(events: list[dict], server_ids: list[str]) -> list[dict]:
    starts, windows = {}, []
    for event in events:
        name = str(event.get("tool") or "")
        key = (event.get("step"), name)
        if event.get("type") == "tool_started":
            starts[key] = event["observed_ns"]
        elif event.get("type") == "tool_call" and any(name.startswith(sid + "__") for sid in server_ids):
            windows.append({"start": starts.get(key, event["observed_ns"]), "end": event["observed_ns"],
                            "tool": name, "ok": event.get("ok") is True})
    return windows


def verify(root: Path, phase: int, manifest: dict, summary: dict, previous: dict | None) -> dict:
    truth = read_json(root / "control" / f"truth-{phase}.json")
    events = [json.loads(line) for line in (root / f"events-{phase}.jsonl").read_text(encoding="utf-8").splitlines()]
    audit = [json.loads(line) for line in (root / "control/http.jsonl").read_text(encoding="utf-8").splitlines()]
    audit = [row for row in audit if row["phase"] == phase]
    after, before = summary["after"], summary["before"]
    configs = after["mcp_config"].get("servers", [])
    expected_pages = list(range((len(truth) + manifest["page_size"] - 1) // manifest["page_size"]))
    bound_packages = {}
    for name, package in after["packages"].items():
        directory = Path(package["directory"]).resolve()
        bound_servers = []
        for config in configs:
            argv = [config.get("command", ""), *config.get("args", [])]
            launch_directory = root / "project"
            if "--directory" in argv and argv.index("--directory") + 1 < len(argv):
                launch_directory = Path(argv[argv.index("--directory") + 1]).resolve()
            for token in argv:
                try:
                    target = Path(token)
                    if target.suffix.lower() not in {".py", ".js", ".ts", ".mjs", ".cjs", ".ps1", ".sh"}:
                        continue
                    target = (target if target.is_absolute() else launch_directory / target).resolve()
                    if not target.is_file() or not target.is_relative_to(directory):
                        continue
                    relative = target.relative_to(directory)
                    if EXCLUDED_PARTS.intersection(relative.parts):
                        continue
                    digest = file_hash(target)
                    if digest == package["files"].get(relative.as_posix()) == package.get("git_files", {}).get(relative.as_posix()):
                        bound_servers.append(config["id"])
                        break
                except (OSError, ValueError):
                    continue
        if bound_servers:
            bound_packages[name] = {**package, "servers": bound_servers}
    server_ids = sorted({sid for package in bound_packages.values() for sid in package["servers"]})
    windows = tool_windows(events, server_ids)
    successful = [window for window in windows if window["ok"]]
    pages = sorted({row["page_index"] for row in audit if row["status"] == 200 and row["page_index"] is not None
                    and any(win["start"] <= row["time_ns"] <= win["end"] for win in successful)})
    validated = summary.get("validated_packages", {})
    package_ok = bool(bound_packages) and all(
        package.get("git_matches") and not package.get("git_remote") and not package.get("git_status")
        and package["receipt"].get("status") == "verified" and validated.get(name, {}).get("ok") is True
        for name, package in bound_packages.items())
    csv_results = {kind: verify_csv(root / "project/output" / f"{kind}_{phase:02}.csv", truth, kind == "shortages")
                   for kind in ("stock", "shortages")}
    checks = {"completed": summary.get("done", {}).get("ok") is True and summary.get("done", {}).get("answer_status") == "complete"
              and not summary.get("exception"),
              "csv_exact": all(item["ok"] for item in csv_results.values()),
              "native_mcp_all_pages": bool(successful) and pages == expected_pages,
              "published_source_local_git": package_ok,
              "cold_new_chat": summary.get("history_messages") == 0 and summary.get("mcp_before") == [
                  {"id": item["id"], "status": "stopped"} for item in before["mcp_config"].get("servers", [])],
              "clean_process_shutdown": not summary.get("cleanup_errors") and not summary.get("surviving_processes"),
              "frozen_inputs": summary.get("frozen_inputs_unchanged") is True,
              "production_unchanged": summary.get("production_unchanged") is True}
    sources = [source for event in events if event.get("type") == "tool_call" and event.get("ok") is True
               for source in event.get("sources", []) if isinstance(source, dict)
               and source.get("status") == "excerpt" and primary_url(str(source.get("url", "")))]
    if phase == 1:
        reads = [event["observed_ns"] for event in events if event.get("type") == "tool_call" and event.get("ok") is True
                 and any(isinstance(source, dict) and source.get("status") == "excerpt" and primary_url(str(source.get("url", "")))
                         for source in event.get("sources", []))]
        creation = list(summary.get("source_first_seen", {}).values())
        notes = "\n".join(package["sources"] for package in bound_packages.values())
        checks["primary_web_before_source"] = bool(reads and creation and min(reads) < min(creation))
        checks["source_provenance"] = bool(re.search(r"https://(?:github\.com|raw\.githubusercontent\.com)/modelcontextprotocol/|https://modelcontextprotocol\.io/", notes)
                                          and re.search(r"(?i)license|лиценз|MIT", notes)
                                          and re.search(r"(?i)commit|version|верси|ревизи|\bv\d+\.\d+", notes)
                                          and re.search(r"(?i)adapt|измен|адапт|использ|reuse", notes))
    elif phase == 2:
        failures = [win for win in windows if not win["ok"] and any(
            row["status"] == 410 and win["start"] <= row["time_ns"] <= win["end"] for row in audit)]
        old = previous["after"]["packages"] if previous else {}
        checks["observed_failure_then_repair"] = bool(failures and any(win["start"] > failures[0]["end"] for win in successful))
        checks["same_integration_new_version"] = bool(bound_packages) and any(
            name in old and old[name]["active"]["revision"] != package["active"]["revision"]
            and old[name]["files"] != package["files"] for name, package in bound_packages.items())
        checks["same_server_identity"] = bool(previous) and {item["id"] for item in configs} == {
            item["id"] for item in previous["after"]["mcp_config"].get("servers", [])}
    else:
        checks["unchanged_saved_integration"] = bool(previous) and before["active"] == after["active"] == previous["after"]["active"] \
            and before["packages"] == after["packages"] == previous["after"]["packages"] \
            and before["mcp_config"] == after["mcp_config"] == previous["after"]["mcp_config"]
        checks["fresh_process"] = bool(previous) and summary["process_identity"] != previous["process_identity"]
    return {"ok": all(checks.values()), "checks": checks, "csv": csv_results, "pages": pages,
            "expected_pages": expected_pages, "mcp_calls": windows, "bound_packages": list(bound_packages),
            "primary_sources": sources}
