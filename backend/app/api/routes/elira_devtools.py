from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/elira", tags=["elira-devtools"])

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


class FsCreatePayload(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class FsDeletePayload(BaseModel):
    path: str = Field(min_length=1)


class FsRenamePayload(BaseModel):
    old_path: str = Field(min_length=1)
    new_path: str = Field(min_length=1)


class PatchPlanPayload(BaseModel):
    goal: str = Field(min_length=1)
    current_path: Optional[str] = None
    current_content: Optional[str] = None
    staged_paths: List[str] = []


def resolve_project_path(rel_path: str) -> Path:
    target = (PROJECT_ROOT / rel_path).resolve()

    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside project root")

    parts = set(target.parts)
    if parts & BLOCKED_PARTS:
        raise HTTPException(status_code=403, detail="Path points to blocked area")

    return target


def is_allowed_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & BLOCKED_PARTS:
        return False
    return True


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


@router.get("/project/map")
def project_map(limit: int = 300):
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


@router.post("/fs/create")
def fs_create(payload: FsCreatePayload):
    target = resolve_project_path(payload.path)

    if target.exists():
        raise HTTPException(status_code=409, detail="Target already exists")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content or "", encoding="utf-8")

    return {
        "status": "ok",
        "path": payload.path,
        "action": "create",
    }


@router.post("/fs/delete")
def fs_delete(payload: FsDeletePayload):
    target = resolve_project_path(payload.path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Target not found")

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Only file delete is supported")

    target.unlink()

    return {
        "status": "ok",
        "path": payload.path,
        "action": "delete",
    }


@router.post("/fs/rename")
def fs_rename(payload: FsRenamePayload):
    source = resolve_project_path(payload.old_path)
    target = resolve_project_path(payload.new_path)

    if not source.exists():
        raise HTTPException(status_code=404, detail="Source not found")

    if target.exists():
        raise HTTPException(status_code=409, detail="Target already exists")

    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)

    return {
        "status": "ok",
        "old_path": payload.old_path,
        "new_path": payload.new_path,
        "action": "rename",
    }


@router.post("/patch/plan")
def patch_plan(payload: PatchPlanPayload):
    goal = payload.goal.strip()
    current_path = payload.current_path or ""
    staged_paths = payload.staged_paths or []

    plan_items: List[Dict[str, str]] = []
    notes: List[str] = []

    if current_path:
        plan_items.append({
            "action": "modify",
            "path": current_path,
            "reason": "Текущий открытый файл выбран как основной кандидат на изменение.",
        })

    for path in staged_paths[:10]:
        if path != current_path:
            plan_items.append({
                "action": "modify",
                "path": path,
                "reason": "Файл уже staged, значит участвует в текущем наборе изменений.",
            })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "создай", "добав", "новый файл", "component", "компонент"]):
        suggested_name = "frontend/src/components/NewFeaturePanel.jsx"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Задача выглядит как добавление новой UI-функции или компонента.",
            })

    if any(word in goal_l for word in ["route", "router", "endpoint", "api", "backend", "роут", "эндпоинт"]):
        suggested_name = "backend/app/api/routes/new_feature.py"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Задача затрагивает backend API или роутинг.",
            })

    if not plan_items:
        plan_items.append({
            "action": "inspect",
            "path": current_path or "project",
            "reason": "Нужно сначала уточнить затронутую область проекта.",
        })

    notes.append("Сначала сделай preview diff до apply.")
    notes.append("Для multi-file изменений лучше stage нужные файлы заранее.")
    notes.append("После apply выполни verify и проверь history.")

    return {
        "status": "ok",
        "goal": goal,
        "items": plan_items,
        "notes": notes,
    }
