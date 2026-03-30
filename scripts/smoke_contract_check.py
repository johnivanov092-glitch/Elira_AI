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
from app.core.web import DEFAULT_SEARCH_ENGINES, SUPPORTED_SEARCH_ENGINES  # noqa: E402
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
    "/api/runtime/status",
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
RUNTIME_STATUS_REQUIRED_KEYS = {
    "ok",
    "python_executable",
    "process_id",
    "cwd",
    "data_dir",
    "active_db_path",
    "active_chat_count",
    "storage_mode",
    "persona_version",
    "backend_origin",
    "primary_engine",
    "fallback_engines",
    "available_engines",
    "supported_engines",
    "api_keys_present",
    "degraded_mode",
    "web_warnings",
    "warning",
}
EXPECTED_SEARCH_ENGINES = ("tavily", "duckduckgo", "wikipedia")
LEGACY_ENGINE_SNIPPETS = {
    '"brave"',
    '("duckduckgo", "searxng", "wikipedia", "bing", "google")',
    '("duckduckgo", "searxng", "wikipedia", "bing", "google", "yandex")',
    '{"id": "searxng"',
    '{"id": "brave"',
    '{"id": "bing"',
    '{"id": "google"',
    '{"id": "yandex"',
}
SEARCH_ENGINE_RUNTIME_FILES = {
    BACKEND_ROOT / "app" / "core" / "web.py",
    BACKEND_ROOT / "app" / "services" / "agents_service.py",
    BACKEND_ROOT / "app" / "services" / "web_multisearch_service.py",
    BACKEND_ROOT / "app" / "services" / "web_service.py",
    BACKEND_ROOT / "app" / "api" / "routes" / "web_search_routes.py",
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


def find_legacy_engine_snippets() -> list[str]:
    hits: list[str] = []
    for path in SEARCH_ENGINE_RUNTIME_FILES:
        if not path.exists():
            continue
        text = read_text(path)
        for snippet in LEGACY_ENGINE_SNIPPETS:
            if snippet in text:
                hits.append(f"{path.relative_to(ROOT)} -> {snippet}")
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


def validate_runtime_status_shape(status: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(status, dict):
        return [f"runtime_status: expected dict, got {type(status).__name__}"]

    missing = sorted(RUNTIME_STATUS_REQUIRED_KEYS - set(status))
    if missing:
        failures.append(f"runtime_status: missing keys {', '.join(missing)}")

    if not isinstance(status.get("process_id"), int):
        failures.append("runtime_status: 'process_id' must be int")
    if not isinstance(status.get("active_chat_count"), int):
        failures.append("runtime_status: 'active_chat_count' must be int")
    if not isinstance(status.get("storage_mode"), str):
        failures.append("runtime_status: 'storage_mode' must be string")
    if not isinstance(status.get("active_db_path"), str):
        failures.append("runtime_status: 'active_db_path' must be string")
    if not isinstance(status.get("backend_origin"), str):
        failures.append("runtime_status: 'backend_origin' must be string")
    if not isinstance(status.get("primary_engine"), str):
        failures.append("runtime_status: 'primary_engine' must be string")
    if not isinstance(status.get("fallback_engines"), list):
        failures.append("runtime_status: 'fallback_engines' must be list")
    if not isinstance(status.get("available_engines"), list):
        failures.append("runtime_status: 'available_engines' must be list")
    if not isinstance(status.get("supported_engines"), list):
        failures.append("runtime_status: 'supported_engines' must be list")
    if not isinstance(status.get("api_keys_present"), dict):
        failures.append("runtime_status: 'api_keys_present' must be dict")
    if not isinstance(status.get("degraded_mode"), bool):
        failures.append("runtime_status: 'degraded_mode' must be bool")
    if not isinstance(status.get("web_warnings"), list):
        failures.append("runtime_status: 'web_warnings' must be list")
    if status.get("warning") is not None and not isinstance(status.get("warning"), str):
        failures.append("runtime_status: 'warning' must be string or None")
    if status.get("storage_mode") != "rooted_sqlite":
        failures.append("runtime_status: storage_mode must be rooted_sqlite")

    supported = status.get("supported_engines") or []
    available = status.get("available_engines") or []
    primary = status.get("primary_engine")

    if tuple(DEFAULT_SEARCH_ENGINES) != EXPECTED_SEARCH_ENGINES:
        failures.append(f"runtime_status: DEFAULT_SEARCH_ENGINES must be {EXPECTED_SEARCH_ENGINES}")
    if tuple(SUPPORTED_SEARCH_ENGINES) != EXPECTED_SEARCH_ENGINES:
        failures.append(f"runtime_status: SUPPORTED_SEARCH_ENGINES must be {EXPECTED_SEARCH_ENGINES}")
    if supported and tuple(supported) != EXPECTED_SEARCH_ENGINES:
        failures.append(f"runtime_status: supported_engines must be {EXPECTED_SEARCH_ENGINES}")
    if available and not set(available).issubset(set(EXPECTED_SEARCH_ENGINES)):
        failures.append("runtime_status: available_engines contains unsupported engine ids")
    if primary and primary not in EXPECTED_SEARCH_ENGINES:
        failures.append("runtime_status: primary_engine must be one of supported engines")
    if primary and available and primary not in available:
        failures.append("runtime_status: primary_engine must be present in available_engines")
    if "duckduckgo" not in available:
        failures.append("runtime_status: available_engines must include duckduckgo")
    if "wikipedia" not in available:
        failures.append("runtime_status: available_engines must include wikipedia")
    api_keys_present = status.get("api_keys_present") or {}
    if "brave" in api_keys_present:
        failures.append("runtime_status: api_keys_present must not include brave")
    if "tavily" not in api_keys_present:
        failures.append("runtime_status: api_keys_present must include tavily")

    return failures


def validate_web_engines_shape(payload: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(payload, dict):
        return [f"web_engines: expected dict, got {type(payload).__name__}"]

    engines = payload.get("engines")
    defaults = payload.get("default")

    if not isinstance(engines, list):
        failures.append("web_engines: 'engines' must be list")
        return failures
    if not isinstance(defaults, list):
        failures.append("web_engines: 'default' must be list")
        return failures

    ids = [item.get("id") for item in engines if isinstance(item, dict)]
    if tuple(ids) != EXPECTED_SEARCH_ENGINES:
        failures.append(f"web_engines: engine ids must be {EXPECTED_SEARCH_ENGINES}")
    if tuple(defaults) != EXPECTED_SEARCH_ENGINES:
        failures.append(f"web_engines: default must be {EXPECTED_SEARCH_ENGINES}")
    return failures


def collect_failures() -> dict[str, Any]:
    schema = app.openapi()
    paths = set(schema.get("paths", {}))
    client = TestClient(app)

    vector_status = vector_memory_capability_status()
    screenshot_status = screenshot_capability_status()
    persona_status = client.get("/api/persona/status").json()
    runtime_status = client.get("/api/runtime/status").json()
    web_engines = client.get("/api/web/engines").json()

    return {
        "paths_count": len(paths),
        "vector_status": vector_status,
        "screenshot_status": screenshot_status,
        "persona_status": persona_status,
        "runtime_status": runtime_status,
        "web_engines": web_engines,
        "missing_paths": sorted(REQUIRED_PATHS - paths),
        "raw_fetch_hits": find_raw_relative_fetches(),
        "dead_endpoint_hits": find_dead_endpoint_refs(),
        "component_literal_hits": find_component_endpoint_literals(),
        "legacy_engine_hits": find_legacy_engine_snippets(),
        "capability_failures": [
            *validate_capability_shape("vector_memory", vector_status),
            *validate_capability_shape("screenshot", screenshot_status),
        ],
        "persona_failures": validate_persona_status_shape(persona_status),
        "runtime_failures": validate_runtime_status_shape(runtime_status),
        "web_engine_failures": validate_web_engines_shape(web_engines),
    }


def main() -> int:
    results = collect_failures()

    print("OpenAPI paths:", results["paths_count"])
    print("Vector memory:", results["vector_status"])
    print("Screenshot:", results["screenshot_status"])
    print("Persona status:", json.dumps(results["persona_status"], ensure_ascii=True))
    print("Runtime status:", json.dumps(results["runtime_status"], ensure_ascii=True))
    print("Web engines:", json.dumps(results["web_engines"], ensure_ascii=True))

    failed = False
    failure_sections = (
        ("Missing required backend routes", results["missing_paths"]),
        ("Raw relative fetch() calls outside shared client", results["raw_fetch_hits"]),
        ("Backend endpoint literals inside frontend components", results["component_literal_hits"]),
        ("Dead frontend endpoint references still present", results["dead_endpoint_hits"]),
        ("Legacy search-engine snippets still present", results["legacy_engine_hits"]),
        ("Capability status shape issues", results["capability_failures"]),
        ("Persona status shape issues", results["persona_failures"]),
        ("Runtime status shape issues", results["runtime_failures"]),
        ("Web engine shape issues", results["web_engine_failures"]),
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
