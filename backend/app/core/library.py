"""
library.py — Библиотека файлов профиля.

Каждый файл имеет метаданные (тип, откуда, когда) хранящиеся в
uploads/{profile}/.library.json рядом с самими файлами.

Типы файлов:
  uploaded  — загружен пользователем
  generated — создан моделью (Code Builder / Python Lab)
  upgrade   — улучшенная версия существующего файла
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import UPLOAD_DIR


# ── Метаданные ────────────────────────────────────────────────────────────────

def _meta_path(profile_name: str) -> Path:
    return UPLOAD_DIR / profile_name / ".library.json"


def _load_meta(profile_name: str) -> Dict[str, dict]:
    p = _meta_path(profile_name)
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_meta(profile_name: str, meta: Dict[str, dict]):
    p = _meta_path(profile_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def get_file_meta(profile_name: str, filename: str) -> dict:
    return _load_meta(profile_name).get(filename, {
        "type": "uploaded",
        "added_at": "",
        "description": "",
        "origin": "",
    })


def set_file_meta(profile_name: str, filename: str, file_type: str,
                  description: str = "", origin: str = ""):
    meta = _load_meta(profile_name)
    meta[filename] = {
        "type": file_type,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "description": description,
        "origin": origin,
    }
    _save_meta(profile_name, meta)


def remove_file_meta(profile_name: str, filename: str):
    meta = _load_meta(profile_name)
    meta.pop(filename, None)
    _save_meta(profile_name, meta)


# ── Получение файлов библиотеки ───────────────────────────────────────────────

def get_library_files(profile_name: str) -> List[dict]:
    """
    Возвращает список файлов профиля с метаданными, отсортированных по дате добавления.
    Каждый элемент: {name, path, type, added_at, description, origin, size}
    """
    profile_dir = UPLOAD_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    meta = _load_meta(profile_name)
    result = []
    for f in sorted(profile_dir.glob("*")):
        if f.name.startswith(".") or f.is_dir():
            continue
        fm = meta.get(f.name, {
            "type": "uploaded",
            "added_at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "description": "",
            "origin": "",
        })
        result.append({
            "name":        f.name,
            "path":        f,
            "type":        fm.get("type", "uploaded"),
            "added_at":    fm.get("added_at", ""),
            "description": fm.get("description", ""),
            "origin":      fm.get("origin", ""),
            "size":        f.stat().st_size,
        })
    # Сортировка: сначала закреплённые (uploaded pinned), потом по дате desc
    result.sort(key=lambda x: x["added_at"], reverse=True)
    return result


# ── Сохранение сгенерированного файла в библиотеку ───────────────────────────

def save_generated_to_library(
    profile_name: str,
    filename: str,
    content: str,
    file_type: str = "generated",   # "generated" или "upgrade"
    description: str = "",
    origin: str = "",               # откуда: "code_builder", "python_lab" и т.д.
) -> Path:
    """
    Сохраняет файл созданный моделью в библиотеку профиля.
    Если тип "upgrade" — добавляет префикс upgrade_ к имени.
    """
    profile_dir = UPLOAD_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Формируем имя файла
    if file_type == "upgrade" and not filename.startswith("upgrade_"):
        save_name = f"upgrade_{filename}"
    else:
        save_name = filename

    # Если файл с таким именем уже есть — добавляем timestamp
    target = profile_dir / save_name
    if target.exists():
        stem = Path(save_name).stem
        suffix = Path(save_name).suffix
        ts = datetime.now().strftime("%m%d_%H%M")
        save_name = f"{stem}_{ts}{suffix}"
        target = profile_dir / save_name

    target.write_text(content, encoding="utf-8")
    set_file_meta(profile_name, save_name, file_type, description, origin)
    return target


# ── Тип → иконка / цвет ──────────────────────────────────────────────────────

TYPE_ICON = {
    "uploaded":  "📄",
    "generated": "🤖",
    "upgrade":   "⚡",
}

TYPE_COLOR = {
    "uploaded":  "rgba(59,130,246,.15)",
    "generated": "rgba(16,185,129,.15)",
    "upgrade":   "rgba(251,191,36,.15)",
}

TYPE_LABEL = {
    "uploaded":  "Загружен",
    "generated": "Создан моделью",
    "upgrade":   "Улучшение",
}
