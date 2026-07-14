"""
files.py — извлечение текста из файлов.

Поддержка: PDF, DOCX, XLSX, ZIP, BAS, VBA, CLS, FRM, RSC, и все текстовые.
"""
import io
import zipfile
from pathlib import Path

# Расширения которые читаем как текст
TEXT_EXTS = {
    ".txt", ".md", ".json", ".js", ".jsx", ".ts", ".tsx", ".py",
    ".css", ".html", ".htm", ".yml", ".yaml", ".xml", ".csv",
    ".log", ".ini", ".toml", ".cfg", ".conf", ".env",
    ".bas", ".vbs", ".vba", ".cls", ".frm", ".rsc",  # VBA / Basic
    ".bat", ".cmd", ".ps1", ".sh",  # Скрипты
    ".sql", ".rb", ".php", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".swift", ".kt", ".r", ".m", ".lua",
    ".pl", ".tcl", ".asm",  # Языки
    ".gitignore", ".dockerfile", ".makefile",
    ".sln", ".csproj", ".pom", ".gradle",  # Проектные
}


def _extract_pdf(data: bytes, max_chars: int = 50000) -> str:
    """Умное извлечение: pypdf → pdfplumber → OCR."""
    try:
        from app.application.pdf.runtime import extract_pdf_smart
        result = extract_pdf_smart(data, max_chars)
        text = result.get("text", "")
        # Добавляем таблицы в текст
        tables = result.get("tables", [])
        if tables:
            table_lines = ["\n\n=== ТАБЛИЦЫ ==="]
            for t in tables[:5]:
                headers = t.get("headers", [])
                rows = t.get("rows", [])
                table_lines.append(f"\nТаблица (стр. {t.get('page', '?')}):")
                if headers:
                    table_lines.append(" | ".join(str(h or "") for h in headers))
                    table_lines.append("-" * 40)
                for row in rows[:20]:
                    table_lines.append(" | ".join(str(c or "") for c in row))
            text += "\n".join(table_lines)
        if result.get("ocr_used"):
            text = f"[OCR распознавание]\n{text}"
        return text[:max_chars]
    except ImportError:
        # Fallback на простой pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            parts, total = [], 0
            for page in reader.pages:
                t = page.extract_text() or ""
                if total + len(t) > max_chars:
                    parts.append(t[:max_chars - total])
                    break
                parts.append(t)
                total += len(t)
            return "\n\n".join(parts)
        except ImportError:
            return "[pypdf не установлен: pip install pypdf]"
    except Exception as e:
        return f"[PDF ошибка: {e}]"


def _extract_docx(data: bytes, max_chars: int = 30000) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        parts = []
        total = 0
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                if total + len(text) > max_chars:
                    break
                parts.append(text)
                total += len(text)
        return "\n".join(parts)
    except ImportError:
        return "[python-docx не установлен: pip install python-docx]"
    except Exception as e:
        return f"[DOCX ошибка: {e}]"


def _extract_xlsx(data: bytes, max_chars: int = 30000) -> str:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        total = 0
        for sheet in wb.sheetnames[:5]:  # Макс 5 листов
            ws = wb[sheet]
            parts.append(f"=== Лист: {sheet} ===")
            for row in ws.iter_rows(max_row=200, values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                line = " | ".join(cells)
                if total + len(line) > max_chars:
                    break
                parts.append(line)
                total += len(line)
        wb.close()
        return "\n".join(parts)
    except ImportError:
        return "[openpyxl не установлен: pip install openpyxl]"
    except Exception as e:
        return f"[XLSX ошибка: {e}]"


def _extract_xls(data: bytes, max_chars: int = 30000) -> str:
    """Legacy .xls (Excel ≤2003). openpyxl не читает .xls — используем xlrd."""
    try:
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        parts: list[str] = []
        total = 0
        for sheet in book.sheets()[:5]:  # макс 5 листов
            parts.append(f"=== Лист: {sheet.name} ===")
            for r in range(min(sheet.nrows, 200)):
                cells = [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
                line = " | ".join(cells)
                if total + len(line) > max_chars:
                    break
                parts.append(line)
                total += len(line)
        return "\n".join(parts)
    except ImportError:
        return "[xlrd не установлен: pip install xlrd]"
    except Exception as e:
        return f"[XLS ошибка: {e}]"


def _extract_pptx(data: bytes, max_chars: int = 30000) -> str:
    """PowerPoint .pptx — собираем текст со всех фигур каждого слайда."""
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(data))
        parts: list[str] = []
        total = 0
        for i, slide in enumerate(prs.slides, 1):
            parts.append(f"=== Слайд {i} ===")
            for shape in slide.shapes:
                if not getattr(shape, "has_text_frame", False):
                    continue
                text = shape.text.strip()
                if not text:
                    continue
                if total + len(text) > max_chars:
                    break
                parts.append(text)
                total += len(text)
            if total >= max_chars:
                break
        return "\n".join(parts)
    except ImportError:
        return "[python-pptx не установлен: pip install python-pptx]"
    except Exception as e:
        return f"[PPTX ошибка: {e}]"


def _extract_zip(data: bytes, max_chars: int = 30000) -> str:
    """Открывает ZIP и читает текстовые файлы внутри."""
    try:
        parts = []
        total = 0
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            parts.append(f"ZIP содержит {len(zf.namelist())} файлов:")
            for name in zf.namelist()[:30]:  # Макс 30 файлов
                ext = Path(name).suffix.lower()
                size = zf.getinfo(name).file_size
                parts.append(f"  - {name} ({size} байт)")

                # Читаем текстовые файлы
                if ext in TEXT_EXTS and size < 100_000:
                    try:
                        content = zf.read(name).decode("utf-8", errors="replace")
                        if total + len(content) > max_chars:
                            content = content[:max_chars - total]
                        parts.append(f"\n--- {name} ---\n{content}")
                        total += len(content)
                    except Exception:
                        pass

                if total > max_chars:
                    break

        return "\n".join(parts)
    except Exception as e:
        return f"[ZIP ошибка: {e}]"


def _extract_text(data: bytes, max_chars: int = 30000) -> str:
    """Читает текст, пробуя несколько кодировок (UTF-8 → CP1251 → CP866)."""
    if not data:
        return ""
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866", "latin-1"):
        try:
            text = data.decode(enc)
            # Проверяем что текст читаемый (нет замен)
            if "\ufffd" not in text[:500]:
                return text[:max_chars]
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")[:max_chars]


# Canonical audio-container allowlist (single source of truth — chat.py and
# library/runtime.py import THIS tuple, no second copy). `.mp4` is a WhatsApp voice
# container: v1 semantics = extract & transcribe its audio track via STT, exactly
# like .m4a/.webm. No video/frame analysis — the STT service decodes the container.
_AUDIO_EXTS = (".ogg", ".oga", ".opus", ".wav", ".mp3", ".m4a", ".mp4", ".flac", ".webm", ".aac")
_AUDIO_STT_TIMEOUT_SECONDS = 3600


def _transcribe_audio(contents: bytes, filename: str) -> str:
    """Расшифровать аудио-вложение через self-hosted whisper (STT).

    whisper сам сегментирует длинное аудио по паузам/границам фраз. CPU-only
    small/int8 на реальном 51-минутном WhatsApp MP4 превысил 30 минут, поэтому
    даём согласованный, но всё ещё bounded 60-минутный бюджет.
    Ошибку STT возвращаем текстом, чтобы вложение не падало с 500.
    """
    from app.application.voice.runtime import transcribe

    try:
        text = transcribe(
            contents,
            filename=filename or "audio",
            language=None,
            timeout=_AUDIO_STT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — surface STT failure as attachment text
        return f"[не удалось расшифровать аудио: {exc}]"
    return (text or "").strip() or "[аудио распознано, но текст пустой]"


def extract_file(filename: str, contents: bytes) -> dict:
    """Извлекает текст из любого поддерживаемого файла."""
    filename = (filename or "").strip()
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        text = _extract_pdf(contents)
    elif ext in (".docx", ".doc"):
        text = _extract_docx(contents)
    elif ext == ".xls":
        text = _extract_xls(contents)
    elif ext in (".xlsx", ".xlsm"):
        text = _extract_xlsx(contents)
    elif ext == ".pptx":
        text = _extract_pptx(contents)
    elif ext == ".zip":
        text = _extract_zip(contents)
    elif ext in _AUDIO_EXTS:
        text = _transcribe_audio(contents, filename)
    else:
        text = _extract_text(contents)

    return {
        "ok": True,
        "filename": filename,
        "size": len(contents),
        "text": text,
        "chars": len(text),
        "type": ext,
    }
