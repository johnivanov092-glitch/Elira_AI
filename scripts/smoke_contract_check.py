from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
FRONTEND_ROOT = ROOT / "frontend" / "src"
COMPONENTS_ROOT = FRONTEND_ROOT / "components"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.memory import vector_memory_capability_status  # noqa: E402
from app.main import app  # noqa: E402
from app.services.skills_service import screenshot_capability_status  # noqa: E402


REQUIRED_PATHS = {
    "/api/tasks/list",
    "/api/tasks/stats",
    "/api/pipelines/list",
    "/api/dashboard/stats",
    "/api/telegram/config",
    "/api/elira/chats",
    "/api/project-brain/status",
    "/api/persona/status",
}

DEAD_FRONTEND_ENDPOINTS = {
    "/api/elira/projects",
    "/api/elira/run-history/list",
    "/api/elira/autocode/suggest",
    "/api/elira/autocode/loop",
}

ALLOWED_FETCH_FILES = {
    FRONTEND_ROOT / "api" / "client.js",
    FRONTEND_ROOT / "api" / "ide.js",
}

CODE_FILE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
RAW_RELATIVE_FETCH_RE = re.compile(r"fetch\s*\(\s*([\"'`])/api/")
COMPONENT_ENDPOINT_LITERAL_RE = re.compile(r"[\"'`]/api/")
CAPABILITY_REQUIRED_KEYS = {"feature", "available", "reason", "missing_packages", "hint"}
PERSONA_STATUS_REQUIRED_KEYS = {
    "ok",
    "persona_name",
    "active_version",
    "status",
    "last_evolution_at",
    "quarantine_candidates",
    "latest_traits",
    "model_consistency",
}


def iter_frontend_code_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.suffix in CODE_FILE_SUFFIXES]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def find_raw_relative_fetches() -> list[str]:
    hits: list[str] = []
    for path in iter_frontend_code_files(FRONTEND_ROOT):
        if path in ALLOWED_FETCH_FILES:
            continue
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if RAW_RELATIVE_FETCH_RE.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")
    return hits


def find_dead_endpoint_refs() -> list[str]:
    hits: list[str] = []
    for path in iter_frontend_code_files(FRONTEND_ROOT):
        text = read_text(path)
        for endpoint in DEAD_FRONTEND_ENDPOINTS:
            if endpoint in text:
                hits.append(f"{path.relative_to(ROOT)} -> {endpoint}")
    return hits


def find_component_endpoint_literals() -> list[str]:
    hits: list[str] = []
    for path in iter_frontend_code_files(COMPONENTS_ROOT):
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if COMPONENT_ENDPOINT_LITERAL_RE.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")
    return hits


def validate_capability_shape(name: str, status: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(status, dict):
        return [f"{name}: expected dict, got {type(status).__name__}"]

    missing_keys = sorted(CAPABILITY_REQUIRED_KEYS - set(status))
    if missing_keys:
        failures.append(f"{name}: missing keys {', '.join(missing_keys)}")

    available = status.get("available")
    missing_packages = status.get("missing_packages")
    hint = status.get("hint")

    if not isinstance(available, bool):
        failures.append(f"{name}: 'available' must be a bool")
    if not isinstance(missing_packages, list):
        failures.append(f"{name}: 'missing_packages' must be a list")
    if hint is not None and not isinstance(hint, str):
        failures.append(f"{name}: 'hint' must be a string or None")

    if isinstance(available, bool) and isinstance(missing_packages, list):
        if available and missing_packages:
            failures.append(f"{name}: available capability cannot report missing packages")
        if not available and not missing_packages:
            failures.append(f"{name}: unavailable capability should report missing packages")

    return failures


def validate_persona_status_shape(status: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(status, dict):
        return [f"persona_status: expected dict, got {type(status).__name__}"]

    missing = sorted(PERSONA_STATUS_REQUIRED_KEYS - set(status))
    if missing:
        failures.append(f"persona_status: missing keys {', '.join(missing)}")

    if not isinstance(status.get("active_version"), int):
        failures.append("persona_status: 'active_version' must be int")
    if not isinstance(status.get("quarantine_candidates"), int):
        failures.append("persona_status: 'quarantine_candidates' must be int")
    if not isinstance(status.get("latest_traits"), list):
        failures.append("persona_status: 'latest_traits' must be a list")
    if not isinstance(status.get("model_consistency"), list):
        failures.append("persona_status: 'model_consistency' must be a list")

    return failures


def collect_failures() -> dict[str, Any]:
    schema = app.openapi()
    paths = set(schema.get("paths", {}))
    client = TestClient(app)

    vector_status = vector_memory_capability_status()
    screenshot_status = screenshot_capability_status()
    persona_status = client.get("/api/persona/status").json()

    return {
        "paths_count": len(paths),
        "vector_status": vector_status,
        "screenshot_status": screenshot_status,
        "persona_status": persona_status,
        "missing_paths": sorted(REQUIRED_PATHS - paths),
        "raw_fetch_hits": find_raw_relative_fetches(),
        "dead_endpoint_hits": find_dead_endpoint_refs(),
        "component_literal_hits": find_component_endpoint_literals(),
        "capability_failures": [
            *validate_capability_shape("vector_memory", vector_status),
            *validate_capability_shape("screenshot", screenshot_status),
        ],
        "persona_failures": validate_persona_status_shape(persona_status),
    }


def main() -> int:
    results = collect_failures()

    print("OpenAPI paths:", results["paths_count"])
    print("Vector memory:", results["vector_status"])
    print("Screenshot:", results["screenshot_status"])
    print("Persona status:", json.dumps(results["persona_status"], ensure_ascii=True))

    failed = False
    failure_sections = (
        ("Missing required backend routes", results["missing_paths"]),
        ("Raw relative fetch() calls outside shared client", results["raw_fetch_hits"]),
        ("Backend endpoint literals inside frontend components", results["component_literal_hits"]),
        ("Dead frontend endpoint references still present", results["dead_endpoint_hits"]),
        ("Capability status shape issues", results["capability_failures"]),
        ("Persona status shape issues", results["persona_failures"]),
    )

    for title, items in failure_sections:
        if not items:
            continue
        failed = True
        print(f"\n{title}:")
        for item in items:
            print(" -", item)

    if failed:
        return 1

    print("\nSmoke contract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
