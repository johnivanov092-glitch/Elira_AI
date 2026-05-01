from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

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


def resolve_project_path(rel_path: str) -> Tuple[Path | None, str | None]:
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


def build_project_map(limit: int = 300):
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


def fs_create(rel_path: str, content: str = ""):
    target, error = resolve_project_path(rel_path)
    if error:
        return None, error

    if target.exists():
        return None, "already_exists"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")

    return {
        "status": "ok",
        "path": rel_path,
        "action": "create",
    }, None


def fs_delete(rel_path: str):
    target, error = resolve_project_path(rel_path)
    if error:
        return None, error

    if not target.exists():
        return None, "not_found"

    if target.is_dir():
        return None, "is_directory"

    target.unlink()

    return {
        "status": "ok",
        "path": rel_path,
        "action": "delete",
    }, None


def fs_rename(old_path: str, new_path: str):
    source, source_error = resolve_project_path(old_path)
    if source_error:
        return None, source_error

    target, target_error = resolve_project_path(new_path)
    if target_error:
        return None, target_error

    if not source.exists():
        return None, "source_not_found"

    if target.exists():
        return None, "target_exists"

    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)

    return {
        "status": "ok",
        "old_path": old_path,
        "new_path": new_path,
        "action": "rename",
    }, None


def build_patch_plan(
    goal: str,
    current_path: str | None = None,
    staged_paths: List[str] | None = None,
):
    goal = goal.strip()
    current_path = current_path or ""
    staged_paths = staged_paths or []

    plan_items: List[Dict[str, str]] = []
    notes: List[str] = []

    if current_path:
        plan_items.append({
            "action": "modify",
            "path": current_path,
            "reason": "РўРµРєСѓС‰РёР№ РѕС‚РєСЂС‹С‚С‹Р№ С„Р°Р№Р» РІС‹Р±СЂР°РЅ РєР°Рє РѕСЃРЅРѕРІРЅРѕР№ РєР°РЅРґРёРґР°С‚ РЅР° РёР·РјРµРЅРµРЅРёРµ.",
        })

    for path in staged_paths[:10]:
        if path != current_path:
            plan_items.append({
                "action": "modify",
                "path": path,
                "reason": "Р¤Р°Р№Р» СѓР¶Рµ staged, Р·РЅР°С‡РёС‚ СѓС‡Р°СЃС‚РІСѓРµС‚ РІ С‚РµРєСѓС‰РµРј РЅР°Р±РѕСЂРµ РёР·РјРµРЅРµРЅРёР№.",
            })

    goal_l = goal.lower()

    if any(word in goal_l for word in ["create", "СЃРѕР·РґР°Р№", "РґРѕР±Р°РІ", "РЅРѕРІС‹Р№ С„Р°Р№Р»", "component", "РєРѕРјРїРѕРЅРµРЅС‚"]):
        suggested_name = "frontend/src/components/NewFeaturePanel.jsx"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Р—Р°РґР°С‡Р° РІС‹РіР»СЏРґРёС‚ РєР°Рє РґРѕР±Р°РІР»РµРЅРёРµ РЅРѕРІРѕР№ UI-С„СѓРЅРєС†РёРё РёР»Рё РєРѕРјРїРѕРЅРµРЅС‚Р°.",
            })

    if any(word in goal_l for word in ["route", "router", "endpoint", "api", "backend", "СЂРѕСѓС‚", "СЌРЅРґРїРѕРёРЅС‚"]):
        suggested_name = "backend/app/api/routes/new_feature.py"
        if not any(item["path"] == suggested_name for item in plan_items):
            plan_items.append({
                "action": "create",
                "path": suggested_name,
                "reason": "Р—Р°РґР°С‡Р° Р·Р°С‚СЂР°РіРёРІР°РµС‚ backend API РёР»Рё СЂРѕСѓС‚РёРЅРі.",
            })

    if not plan_items:
        plan_items.append({
            "action": "inspect",
            "path": current_path or "project",
            "reason": "РќСѓР¶РЅРѕ СЃРЅР°С‡Р°Р»Р° СѓС‚РѕС‡РЅРёС‚СЊ Р·Р°С‚СЂРѕРЅСѓС‚СѓСЋ РѕР±Р»Р°СЃС‚СЊ РїСЂРѕРµРєС‚Р°.",
        })

    notes.append("РЎРЅР°С‡Р°Р»Р° СЃРґРµР»Р°Р№ preview diff РґРѕ apply.")
    notes.append("Р”Р»СЏ multi-file РёР·РјРµРЅРµРЅРёР№ Р»СѓС‡С€Рµ stage РЅСѓР¶РЅС‹Рµ С„Р°Р№Р»С‹ Р·Р°СЂР°РЅРµРµ.")
    notes.append("РџРѕСЃР»Рµ apply РІС‹РїРѕР»РЅРё verify Рё РїСЂРѕРІРµСЂСЊ history.")

    return {
        "status": "ok",
        "goal": goal,
        "items": plan_items,
        "notes": notes,
    }
