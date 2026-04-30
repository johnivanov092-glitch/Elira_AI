"""Application-layer runtime for the Elira devtools endpoints.

Owns project scanning, import parsing, project-map building, path
resolution, FS operations, and patch-plan building.  The HTTP layer in
``api/routes/elira_devtools.py`` is a thin FastAPI shell that translates
runtime error kinds into HTTPException codes.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(".").resolve()
BLOCKED_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
}
ALLOWED_SCAN_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".html", ".md", ".txt", ".rs"
}


# ───────── Path helpers (HTTP-free) ─────────

def resolve_project_path(rel_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve a relative path under PROJECT_ROOT.

    Returns ``(path, None)`` on success or ``(None, error_kind)`` on
    failure, where *error_kind* is one of ``"outside_root"`` or
    ``"blocked"``.  No HTTPException is raised.
    """
    target = (PROJECT_ROOT / rel_path).resolve()

    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return None, "outside_root"

    parts = set(target.parts)
    if parts & BLOCKED_PARTS:
        return None, "blocked"

    return target, None


def is_allowed_path(path: Path) -> bool:
    parts = set(path.parts)
    return not (parts & BLOCKED_PARTS)


# ───────── Project scanning ─────────

def scan_project_files(limit: int = 1200) -> List[Path]:
    results: List[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        if len(results) >= limit:
            break
        if not path.is_file():
            continue
        if not is_allowed_path(path):
            continue
        if path.suffix.lower() not in ALLOWED_SCAN_SUFFIXES:
            continue
        results.append(path)
    return results


def parse_imports(path: Path) -> List[str]:
    imports: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return imports

    for raw in text.splitlines():
        line = raw.strip()
        if path.suffix == ".py":
            if line.startswith("import "):
                imports.append(line.replace("import ", "", 1).strip())
            elif line.startswith("from "):
                imports.append(line)
        elif path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            if line.startswith("import "):
                imports.append(line)
    return imports[:30]


def build_project_map(limit: int = 300) -> dict:
    files = scan_project_files(limit=max(50, min(limit, 1200)))
    items = []
    ext_counter: Dict[str, int] = defaultdict(int)

    for path in files:
        rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        imports = parse_imports(path)
        suffix = path.suffix.lower() or "file"
        ext_counter[suffix] += 1
        items.append({
            "path": rel,
            "name": path.name,
            "suffix": suffix,
            "imports": imports,
            "size": path.stat().st_size,
        })

    summary = [{"suffix": key, "count": value} for key, value in sorted(ext_counter.items())]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "summary": summary,
    }


# ───────── FS operations (return error_kind instead of raising) ─────────

def fs_create(rel_path: str, content: str) -> Tuple[Optional[dict], Optional[str]]:
    target, err = resolve_project_path(rel_path)
    if err:
        return None, err
    assert target is not None
    if target.exists():
        return None, "already_exists"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"status": "ok", "path": rel_path, "action": "create"}, None


def fs_delete(rel_path: str) -> Tuple[Optional[dict], Optional[str]]:
    target, err = resolve_project_path(rel_path)
    if err:
        return None, err
    assert target is not None
    if not target.exists():
        return None, "not_found"
    if target.is_dir():
        return None, "is_directory"
    target.unlink()
    return {"status": "ok", "path": rel_path, "action": "delete"}, None


def fs_rename(old_path: str, new_path: str) -> Tuple[Optional[dict], Optional[str]]:
    source, err = resolve_project_path(old_path)
    if err:
        return None, err
    target, err2 = resolve_project_path(new_path)
    if err2:
        return None, err2
    assert source is not None and target is not None
    if not source.exists():
        return None, "source_not_found"
    if target.exists():
        return None, "target_exists"
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    return {"status": "ok", "old_path": old_path, "new_path": new_path, "action": "rename"}, None


# ───────── Patch plan builder ─────────

def build_patch_plan(
    goal: str,
    current_path: Optional[str],
    staged_paths: List[str],
) -> dict:
    goal_clean = goal.strip()
    staged_paths = staged_paths or []

    plan_items: List[Dict[str, str]] = []
    notes: List[str] = []

    if current_path:
        plan_items.append({
            "action": "modify",
            "path": current_path,
            "reason": "Current open file selected as primary modify candidate.",
        })

    for path in staged_paths[:10]:
        if path != current_path:
            plan_items.append({
                "action": "modify",
                "path": path,
                "reason": "File already staged and part of current change set.",
            })

    goal_l = goal_clean.lower()

    if any(word in goal_l for word in ["create", "component"]):
        suggested_name = "frontend/src/components/NewFeaturePanel.jsx"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Goal looks like adding a new UI feature or component.",
            })

    if any(word in goal_l for word in ["route", "router", "endpoint", "api", "backend"]):
        suggested_name = "backend/app/api/routes/new_feature.py"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Goal involves backend API or routing.",
            })

    if not plan_items:
        plan_items.append({
            "action": "inspect",
            "path": current_path or "project",
            "reason": "Need to inspect the relevant project area first.",
        })

    notes.append("Do preview diff before apply.")
    notes.append("For multi-file changes stage needed files in advance.")
    notes.append("After apply run verify and check history.")

    return {
        "status": "ok",
        "goal": goal_clean,
        "items": plan_items,
        "notes": notes,
    }
