"""files.py — загрузка, чтение, удаление файлов + Project Analyzer Pro."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd
from pypdf import PdfReader

from .config import UPLOAD_DIR, CHAT_DIR, OUTPUT_DIR
from .memory import add_memory


# ── Утилиты ───────────────────────────────────────────────────────────────────
def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def truncate_text(text: str, max_chars: int = 12000) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_chars else text[:max_chars] + "\n\n[Текст обрезан]"


def normalize_path(input_path: str) -> Path:
    return Path(input_path.strip().strip('"')).expanduser()


def should_auto_save_memory(text: str) -> bool:
    low = (text or "").lower()
    if len(low) < 180:
        return False
    triggers = ["итог", "вывод", "важно", "рекоменд", "план", "шаг",
                "решение", "ключев", "summary"]
    return any(t in low for t in triggers)


def extract_imports_from_python(text: str) -> List[str]:
    imports = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("import "):
            imports.append(line.replace("import ", "").split(" as ")[0].strip())
        elif line.startswith("from "):
            parts = line.split()
            if len(parts) >= 2:
                imports.append(parts[1].strip())
    return imports


# ── Чтение файлов ─────────────────────────────────────────────────────────────
# ── Папка загрузок профиля ────────────────────────────────────────────────────
def get_profile_upload_dir(profile_name: str) -> Path:
    """Каждый профиль хранит файлы в своей подпапке uploads/{profile}/"""
    d = UPLOAD_DIR / profile_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_json_file(path: Path) -> str:
    try:
        return json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=2)
    except Exception:
        return read_text_file(path)


def read_csv_file(path: Path) -> str:
    try:
        return pd.read_csv(path).head(200).to_csv(index=False)
    except Exception:
        return read_text_file(path)


def read_excel_file(path: Path) -> str:
    try:
        return pd.read_excel(path).head(200).to_csv(index=False)
    except Exception as e:
        return f"Ошибка чтения Excel: {e}"


def read_pdf_file(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        return f"Ошибка чтения PDF: {e}"


def read_file_content(path: Path) -> str:
    suffix = path.suffix.lower()

    # Известные текстовые форматы
    _TEXT_SUFFIXES = {
        ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx",
        ".html", ".css", ".log", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".sql", ".sh", ".bat", ".cmd", ".ps1", ".r",
        ".bas", ".vbs", ".vba", ".cls", ".frm",          # VB/VBA
        ".rsc",                                            # MikroTik RouterOS
        ".backup", ".bak", ".dump",                        # бэкапы (часто текстовые)
        ".xml", ".svg", ".env", ".gitignore",
        ".dockerfile", ".makefile", ".cmake",
        ".tsv", ".conf", ".properties",
    }

    if suffix in _TEXT_SUFFIXES:
        return read_text_file(path)
    if suffix == ".json":
        return read_json_file(path)
    if suffix == ".csv":
        return read_csv_file(path)
    if suffix in {".xlsx", ".xls"}:
        return read_excel_file(path)
    if suffix == ".pdf":
        return read_pdf_file(path)

    # Fallback: пробуем прочитать как текст
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.strip():
            non_printable = sum(1 for c in text[:2000] if ord(c) < 32 and c not in '\n\r\t')
            if non_printable / max(len(text[:2000]), 1) < 0.2:
                return text
        return f"Файл {path.name} ({suffix}) — бинарный. Размер: {path.stat().st_size:,} байт."
    except Exception:
        return f"Файл {path.name} ({suffix}) загружен. Размер: {path.stat().st_size:,} байт."


# ── Загрузка / удаление ───────────────────────────────────────────────────────
def save_uploaded_file(uploaded_file, profile_name: str = "default") -> Path:
    path = get_profile_upload_dir(profile_name) / uploaded_file.name
    path.write_bytes(uploaded_file.read())
    return path


def build_uploaded_signature(files) -> str:
    if not files:
        return ""
    return "|".join(f"{f.name}:{getattr(f, 'size', 0)}" for f in files)


def process_uploaded_files(files, profile_name: str) -> Dict[str, Any]:
    if not files:
        return {"uploaded_files": [], "file_context": "", "last_uploaded_signature": ""}

    pieces, saved_names = [], []
    for file in files:
        path = save_uploaded_file(file, profile_name)
        saved_names.append(path.name)
        content = truncate_text(read_file_content(path), 9000)
        pieces.append(f"\n\n===== ФАЙЛ: {path.name} =====\n{content}")
        add_memory(
            f"FILE: {path.name}\n{content[:3000]}",
            source=f"file:{path.name}",
            memory_type="file",
            profile_name=profile_name,
        )

    file_context = truncate_text("\n".join(pieces), 25000)
    return {
        "uploaded_files": saved_names,
        "file_context": file_context,
        "last_uploaded_signature": build_uploaded_signature(files),
    }

def load_profile_file_context(profile_name: str) -> str:
    """
    Читает все файлы профиля с диска и возвращает file_context.
    Вызывается при смене профиля или после перезапуска Streamlit.
    """
    profile_dir = get_profile_upload_dir(profile_name)
    files = sorted(profile_dir.glob("*"))
    if not files:
        return ""
    pieces = []
    for fp in files:
        content = truncate_text(read_file_content(fp), 9000)
        pieces.append(f"\n\n===== ФАЙЛ: {fp.name} =====\n{content}")
    return truncate_text("\n".join(pieces), 25000)


def delete_uploaded_file(filename: str, profile_name: str = "default", uploaded_files: List[str] | None = None) -> Dict[str, Any]:
    path = get_profile_upload_dir(profile_name) / filename
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    current_files = list(uploaded_files or [])
    current_files = [f for f in current_files if f != filename]
    file_context = load_profile_file_context(profile_name)
    return {
        "uploaded_files": current_files,
        "file_context": file_context,
        "last_uploaded_signature": "" if not file_context else None,
    }


def delete_all_uploaded_files(profile_name: str = "default") -> Dict[str, Any]:
    profile_dir = get_profile_upload_dir(profile_name)
    for f in profile_dir.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
    return {"file_context": "", "uploaded_files": [], "last_uploaded_signature": ""}


def list_uploaded_files(profile_name: str = "default") -> List[str]:
    profile_dir = get_profile_upload_dir(profile_name)
    return sorted([p.name for p in profile_dir.glob("*") if p.is_file()])


# ── Чаты ──────────────────────────────────────────────────────────────────────
def sanitize_chat_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or f"chat_{now_stamp()}"


def _chat_folder_path(folder: str | None = None) -> Path:
    folder = (folder or "Общее").strip()
    if folder in {"", ".", "Общее", "default"}:
        return CHAT_DIR
    return CHAT_DIR / sanitize_chat_name(folder)


def create_chat_folder(folder: str) -> Path:
    p = _chat_folder_path(folder)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_chat_rel_label(path: Path) -> str:
    try:
        rel = path.relative_to(CHAT_DIR)
        return str(rel).replace("\\", "/")
    except Exception:
        return path.name


def list_chat_folders() -> List[str]:
    folders = ["Общее"]
    for p in sorted(CHAT_DIR.iterdir()):
        if p.is_dir() and p.name not in {"Общее", "default"}:
            folders.append(p.name)
    return folders


def list_chat_files(folder: str | None = None, recursive: bool = True) -> List[Path]:
    base = _chat_folder_path(folder)
    if recursive and folder is None:
        found = list(CHAT_DIR.rglob("*.json"))
    else:
        found = list(base.glob("*.json"))
    files = [p for p in found if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def save_chat(messages: List[Dict], model_name: str, folder: str = "Общее",
              note: str = "", chat_name: str | None = None,
              existing_path: Path | None = None) -> Path:
    target_dir = _chat_folder_path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = existing_path if existing_path else (target_dir / f"{sanitize_chat_name(chat_name or f'chat_{now_stamp()}')}.json")
    if existing_path is None and path.exists():
        path = target_dir / f"{sanitize_chat_name(chat_name or 'chat')}_{now_stamp()}.json"
    payload = {
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "model": model_name,
        "note": note,
        "title": sanitize_chat_name(chat_name or path.stem),
        "messages": messages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def move_chat_file(chat_path: Path, target_folder: str) -> Path:
    target_dir = _chat_folder_path(target_folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    new_path = target_dir / chat_path.name
    if new_path.exists() and new_path.resolve() != chat_path.resolve():
        new_path = target_dir / f"{chat_path.stem}_{now_stamp()}{chat_path.suffix}"
    chat_path.replace(new_path)
    return new_path


def rename_chat_file(chat_path: Path, new_name: str) -> Path:
    safe_name = sanitize_chat_name(new_name)
    new_path = chat_path.with_name(f"{safe_name}{chat_path.suffix}")
    if new_path.exists() and new_path.resolve() != chat_path.resolve():
        new_path = chat_path.with_name(f"{safe_name}_{now_stamp()}{chat_path.suffix}")
    chat_path.replace(new_path)
    try:
        payload = load_chat_file(new_path)
        if payload:
            payload["title"] = safe_name
            new_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return new_path


def delete_chat_file(chat_path: Path) -> bool:
    try:
        if chat_path.exists():
            chat_path.unlink()
        return True
    except Exception:
        return False


def export_chat_as_markdown(messages: List[Dict], model_name: str) -> str:
    lines = [f"# Чат — {datetime.now().strftime('%Y-%m-%d %H:%M')}", f"**Модель:** {model_name}\n"]
    for m in messages:
        role = "👤 Пользователь" if m["role"] == "user" else "🤖 Ассистент"
        lines.append(f"### {role}\n{m['content']}\n")
    return "\n".join(lines)


def load_chat_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def search_in_saved_chats(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    hits = []
    if not query.strip():
        return hits
    q = query.lower()
    for chat_file in list_chat_files():
        payload = load_chat_file(chat_file)
        joined = "\n".join(m.get("content", "") for m in payload.get("messages", []))
        score = sum(1 for word in q.split() if word in joined.lower())
        if score > 0:
            hits.append({
                "file": get_chat_rel_label(chat_file),
                "score": score,
                "content": truncate_text(joined, 3000),
            })
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:max_results]


def format_chat_search_results(results: List[Dict[str, Any]]) -> str:
    return "\n\n".join(
        f"Файл: {item['file']}\nСовпадение: {item['score']}\n{item['content']}"
        for item in results
    ) if results else ""


# ── Отчёты ────────────────────────────────────────────────────────────────────
def save_text_report(text: str, prefix: str = "report") -> Path:
    path = OUTPUT_DIR / f"{prefix}_{now_stamp()}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ── Project Analyzer Pro ──────────────────────────────────────────────────────
_BLOCKED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
                 ".idea", ".streamlit", "dist", "build"}
_ALLOWED_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".js", ".ts", ".tsx", ".jsx", ".html",
    ".css", ".sql", ".csv",
}


def should_include_project_file(path: Path) -> bool:
    if any(part in _BLOCKED_DIRS for part in path.parts) or path.is_dir():
        return False
    return path.suffix.lower() in _ALLOWED_SUFFIXES


def analyze_project_pro(project_root: Path, max_files: int = 300,
                        max_chars_per_file: int = 5000) -> Dict[str, Any]:
    index_rows, dependency_rows, context_blocks = [], [], []
    file_count = 0
    for path in sorted(project_root.rglob("*")):
        if not should_include_project_file(path):
            continue
        try:
            rel = str(path.relative_to(project_root))
            suffix = path.suffix.lower()
            stat = path.stat()
            text = read_file_content(path)
            short_text = truncate_text(text, max_chars_per_file)
            lines = len(text.splitlines()) if text else 0
            imports = extract_imports_from_python(text) if suffix == ".py" else []
            index_rows.append({"path": rel, "type": suffix or "no_ext",
                                "size": stat.st_size, "lines": lines,
                                "imports": ", ".join(imports[:20])})
            for imp in imports[:20]:
                dependency_rows.append({"file": rel, "depends_on": imp})
            context_blocks.append(f"\n\n===== PROJECT FILE: {rel} =====\n{short_text}")
            file_count += 1
            if file_count >= max_files:
                break
        except Exception as e:
            index_rows.append({"path": str(path), "type": "error",
                                "size": 0, "lines": 0, "imports": str(e)})
    summary_lines = [
        f"Путь проекта: {project_root}",
        f"Файлов в индексе: {len(index_rows)}", "",
    ] + [f"- {r['path']} | {r['type']} | {r['lines']} lines | {r['size']} bytes"
         for r in index_rows[:250]]
    return {
        "index": index_rows,
        "dependencies": dependency_rows,
        "context": truncate_text("\n".join(context_blocks), 50000),
        "summary": "\n".join(summary_lines),
    }
