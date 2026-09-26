"""
pdf_pro.py — продвинутая работа с PDF.

Возможности:
  • Извлечение текста (pypdf + pdfplumber fallback)
  • Таблицы (pdfplumber → структурированные данные)
  • OCR для сканированных PDF (pdf2image + pytesseract)
  • PDF → Word конвертация (сохраняет текст + таблицы)
  • Постраничный анализ

Зависимости (ставить по необходимости):
  pip install pypdf pdfplumber pytesseract pdf2image python-docx openpyxl

Для OCR также нужен Tesseract:
  Windows: https://github.com/UB-Mannheim/tesseract/wiki → установщик → добавить в PATH
  Для русского: скачать rus.traineddata в tessdata/
"""
from __future__ import annotations
import io
import logging
import os
import shutil
import time

from app.core.config import GENERATED_DIR
from app.application.pdf.poppler import poppler_options

logger = logging.getLogger(__name__)

# Common Tesseract install locations to fall back to when it isn't on PATH
# (e.g. the UB-Mannheim Windows installer puts it under Program Files but does
# not always add it to PATH).
_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)


def _ensure_tesseract(pytesseract) -> bool:
    """Point pytesseract at the Tesseract binary. Returns True if a usable
    binary is found (on PATH or in a known install dir), False otherwise."""
    if shutil.which("tesseract"):
        return True
    for path in _TESSERACT_CANDIDATES:
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return True
    return False

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# ИЗВЛЕЧЕНИЕ ТЕКСТА (умный: pypdf → pdfplumber → OCR)
# ═══════════════════════════════════════════════════════════════

def extract_pdf_smart(data: bytes, max_chars: int = 50000) -> dict:
    """Extract each page independently; native text must not hide scanned pages."""
    max_chars = max(1, int(max_chars))
    pages, tables, page_count, errors = _native_pdf_pages(data, max_chars)
    if not pages:
        server_pages, server_errors = _server_ocr_pages(data, [])
        errors.extend(server_errors)
        pages = [{**page, "method": "ocr-server"} for _, page in sorted(server_pages.items())]
        page_count = max((page["page"] for page in pages), default=page_count)
        if not pages:
            text = _try_ocr(data, max_chars)
            return {"text": text, "tables": [], "pages": page_count,
                    "method": "ocr" if text else "", "ocr_used": bool(text),
                    "page_results": [], "errors": errors, "truncated": len(text) >= max_chars}
    by_number = {page["page"]: page for page in pages}
    pending = [page["page"] for page in pages
               if page["method"] != "ocr-server" and len(page["text"].strip()) < 100]
    if pending:
        server_pages, server_errors = _server_ocr_pages(data, pending)
        errors.extend(server_errors)
        unresolved = []
        for number in pending:
            page = by_number[number]
            candidate = server_pages.get(number)
            if candidate:
                page["errors"].extend(candidate.get("errors", []))
            if candidate and candidate.get("text", "").strip():
                if len(candidate["text"].strip()) > len(page["text"].strip()):
                    page.update(candidate, method="ocr-server", errors=page["errors"])
            else:
                unresolved.append(number)
        local_pages = _try_ocr_pages(data, unresolved, max_chars) if unresolved else {}
        for number in unresolved:
            page = by_number[number]
            local_text = local_pages.get(number, "")
            if len(local_text.strip()) > len(page["text"].strip()):
                page.update(text=local_text, method="ocr")
            else:
                error = {"page": number, "code": "ocr_no_text", "message": "OCR did not recover additional text"}
                page["errors"].append(error)
                errors.append(error)

    parts, page_results, total = [], [], 0
    truncated = len(pages) < page_count
    for page in pages:
        text = page["text"].strip()
        prefix = f"--- Страница {page['page']} ---\n" if text else ""
        separator = "\n\n" if parts and text else ""
        remaining = max_chars - total
        rendered = (separator + prefix + text)[:remaining]
        if text:
            parts.append(rendered)
            total += len(rendered)
        text_budget = max(0, len(rendered) - len(separator) - len(prefix))
        metadata = {key: value for key, value in page.items() if key != "text"}
        metadata["chars"] = min(len(text), text_budget)
        metadata["blocks"] = _bounded_blocks(page.get("blocks", []), text_budget)
        page_results.append(metadata)
        if total >= max_chars:
            truncated = text_budget < len(text) or page["page"] < page_count
            break
    methods = list(dict.fromkeys(page["method"] for page in page_results if page["chars"]))
    return {
        "text": "".join(parts), "tables": tables, "pages": page_count,
        "method": "+".join(methods),
        "ocr_used": any(method.startswith("ocr") for method in methods),
        "page_results": page_results, "errors": errors, "truncated": truncated,
    }


def _bounded_blocks(blocks: list, max_chars: int) -> list[dict]:
    bounded = []
    for block in blocks:
        text = block.get("text")
        if not isinstance(text, str) or not text or max_chars <= 0:
            continue
        bounded.append({**block, "text": text[:max_chars]})
        max_chars -= len(bounded[-1]["text"])
    return bounded


def _native_pdf_pages(data: bytes, max_chars: int) -> tuple[list, list, int, list]:
    pages, tables, errors = [], [], []
    count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        count = len(reader.pages)
        total = 0
        for index, source in enumerate(reader.pages, 1):
            page = {"page": index, "text": "", "method": "pypdf", "errors": [], "blocks": []}
            try:
                page["text"] = (source.extract_text() or "")[:max_chars - total]
            except Exception as exc:
                logger.warning("pypdf page %s failed: %s", index, exc)
                error = {"page": index, "code": "native_text_failed", "message": str(exc)}
                page["errors"].append(error)
                errors.append(error)
            pages.append(page)
            total += len(page["text"])
            if total >= max_chars:
                break
    except Exception as exc:
        logger.warning("pypdf failed: %s", exc)
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            count = max(count, len(pdf.pages))
            total = 0
            for index, source in enumerate(pdf.pages, 1):
                if index > len(pages):
                    pages.append({"page": index, "text": "", "method": "pdfplumber", "errors": [], "blocks": []})
                page = pages[index - 1]
                try:
                    text = (source.extract_text() or "")[:max_chars - total]
                    if len(text.strip()) > len(page["text"].strip()):
                        page.update(text=text, method="pdfplumber")
                    for table_index, table in enumerate(source.extract_tables()):
                        if table:
                            tables.append({"page": index, "index": table_index,
                                           "headers": table[0] or [], "rows": table[1:],
                                           "row_count": len(table) - 1})
                except Exception as exc:
                    logger.warning("pdfplumber page %s failed: %s", index, exc)
                total += len(page["text"])
                if total >= max_chars:
                    pages = pages[:index]
                    break
    except Exception as exc:
        logger.warning("pdfplumber failed: %s", exc)
    return pages, tables, count, errors


def _server_ocr_pages(data: bytes, numbers: list[int]) -> tuple[dict, list]:
    """Send only sparse pages; translate subset page numbers back to the PDF."""
    try:
        from app.infrastructure.llm.vision_ocr import ocr_document_result

        if numbers:
            from pypdf import PdfReader, PdfWriter
            reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
            for number in numbers:
                writer.add_page(reader.pages[number - 1])
            output = io.BytesIO()
            writer.write(output)
            data = output.getvalue()
        result = ocr_document_result("document.pdf", data)
    except Exception as exc:
        logger.warning("server OCR failed: %s", exc)
        result = None
    if result is None:
        return {}, [{"code": "server_ocr_unavailable", "message": "Server OCR failed; local fallback attempted"}]

    def remap(error: dict) -> dict:
        number = error.get("page")
        if type(number) is int and 1 <= number <= len(numbers):
            return {**error, "page": numbers[number - 1]}
        return dict(error)

    errors = [remap(error) for error in result.get("errors", [])]
    output_pages = {}
    for page in result.get("pages", []):
        number = page["page"]
        if not numbers or 1 <= number <= len(numbers):
            original = numbers[number - 1] if numbers else number
            output_pages[original] = {**page, "page": original,
                                      "errors": [remap(error) for error in page.get("errors", [])]}
    if not output_pages and len(numbers) <= 1 and result.get("text"):
        number = numbers[0] if numbers else 1
        output_pages[number] = {"page": number, "text": result["text"], "blocks": [], "errors": []}
    return output_pages, errors


def _count_pages(data: bytes) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


def _try_ocr_pages(data: bytes, numbers: list[int], max_chars: int) -> dict[int, str]:
    """Local fallback renders only requested pages, at most ten as before."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except ImportError:
        return {}
    if not _ensure_tesseract(pytesseract):
        logger.warning("OCR skipped: Tesseract binary not found")
        return {}
    output, total = {}, 0
    for number in numbers[:10]:
        images = []
        try:
            images = convert_from_bytes(
                data, dpi=200, first_page=number, last_page=number,
                timeout=60, **poppler_options(),
            )
            if not images:
                continue
            try:
                text = pytesseract.image_to_string(images[0], lang="rus+eng", timeout=30)
            except Exception:
                text = pytesseract.image_to_string(images[0], lang="eng", timeout=30)
            output[number] = text[:max_chars - total]
            total += len(output[number])
        except Exception as exc:
            logger.warning("OCR page %s failed: %s", number, exc)
        finally:
            for image in images:
                image.close()
        if total >= max_chars:
            break
    return output


def _try_ocr(data: bytes, max_chars: int) -> str:
    """Compatibility text wrapper for local OCR preview callers."""
    count = _count_pages(data) or 10
    pages = _try_ocr_pages(data, list(range(1, min(count, 10) + 1)), max_chars)
    return "\n\n".join(
        f"--- OCR страница {number} ---\n{text}" for number, text in pages.items() if text.strip()
    )[:max_chars]


# ═══════════════════════════════════════════════════════════════
# ТАБЛИЦЫ → Excel / CSV
# ═══════════════════════════════════════════════════════════════

def pdf_tables_to_excel(data: bytes, filename: str = "") -> dict:
    """Извлекает все таблицы из PDF и сохраняет в Excel."""
    try:
        import pdfplumber
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return {"ok": False, "error": "pip install pdfplumber openpyxl"}

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            wb = Workbook()
            wb.remove(wb.active)

            table_count = 0
            for i, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                for t_idx, table in enumerate(tables):
                    if not table or len(table) < 2:
                        continue
                    table_count += 1
                    ws = wb.create_sheet(title=f"P{i+1}_T{t_idx+1}")

                    for r, row in enumerate(table, 1):
                        for c, val in enumerate(row or [], 1):
                            cell = ws.cell(row=r, column=c, value=val or "")
                            if r == 1:
                                cell.font = Font(bold=True)
                                cell.fill = PatternFill(start_color="D5E8F0", end_color="D5E8F0", fill_type="solid")

        if table_count == 0:
            return {"ok": False, "error": "Таблицы не найдены в PDF"}

        fname = filename or f"pdf_tables_{int(time.time())}.xlsx"
        if not fname.endswith(".xlsx"):
            fname += ".xlsx"
        path = OUTPUT_DIR / fname
        wb.save(str(path))

        return {"ok": True, "filename": fname, "tables": table_count, "size": path.stat().st_size,
                "download_url": f"/api/skills/download/{fname}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# PDF → WORD КОНВЕРТАЦИЯ
# ═══════════════════════════════════════════════════════════════

def pdf_to_word(data: bytes, filename: str = "") -> dict:
    """Конвертирует PDF → DOCX. Сохраняет текст, заголовки и таблицы."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return {"ok": False, "error": "pip install python-docx"}

    # Извлекаем контент
    result = extract_pdf_smart(data, max_chars=100000)
    text = result.get("text", "")
    tables = result.get("tables", [])

    if not text.strip() and not tables:
        return {"ok": False, "error": "Не удалось извлечь текст из PDF"}

    doc = Document()

    # Стиль
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)

    # Текст: парсим постранично
    current_page = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--- Страница") or stripped.startswith("--- OCR страница"):
            if current_page:
                doc.add_page_break()
            current_page = stripped
            continue
        if not stripped:
            continue

        # Угадываем заголовки (короткие, в верхнем регистре или с большим шрифтом)
        if len(stripped) < 80 and (stripped.isupper() or stripped.endswith(":")):
            doc.add_heading(stripped, level=2)
        else:
            doc.add_paragraph(stripped)

    # Таблицы
    for tbl in tables:
        headers = tbl.get("headers", [])
        rows = tbl.get("rows", [])
        if not headers and not rows:
            continue

        doc.add_paragraph("")  # Отступ
        doc.add_heading(f"Таблица (стр. {tbl.get('page', '?')})", level=3)

        all_rows = [headers] + rows if headers else rows
        max_cols = max(len(r) for r in all_rows) if all_rows else 0
        if max_cols == 0:
            continue

        table = doc.add_table(rows=len(all_rows), cols=max_cols)
        table.style = "Table Grid"

        for r, row in enumerate(all_rows):
            for c, val in enumerate(row[:max_cols]):
                cell = table.rows[r].cells[c]
                cell.text = str(val or "")
                if r == 0 and headers:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True

    fname = filename or f"pdf_converted_{int(time.time())}.docx"
    if not fname.endswith(".docx"):
        fname += ".docx"
    path = OUTPUT_DIR / fname
    doc.save(str(path))

    return {
        "ok": True,
        "filename": fname,
        "size": path.stat().st_size,
        "pages": result.get("pages", 0),
        "tables": len(tables),
        "method": result.get("method", ""),
        "ocr_used": result.get("ocr_used", False),
        "download_url": f"/api/skills/download/{fname}",
    }


# ═══════════════════════════════════════════════════════════════
# ПОСТРАНИЧНЫЙ АНАЛИЗ
# ═══════════════════════════════════════════════════════════════

def analyze_pdf(data: bytes) -> dict:
    """Подробный анализ PDF: страницы, текст, таблицы, изображения."""
    result = extract_pdf_smart(data)

    # Подсчёт слов
    words = len(result["text"].split()) if result["text"] else 0

    # Определяем тип PDF
    pdf_type = "text"
    if result.get("ocr_used"):
        pdf_type = "scanned"
    elif result.get("tables"):
        pdf_type = "tabular"
    elif words < 50:
        pdf_type = "image-heavy"

    return {
        "ok": True,
        "pages": result.get("pages", 0),
        "words": words,
        "chars": len(result.get("text", "")),
        "tables": len(result.get("tables", [])),
        "method": result.get("method", ""),
        "ocr_used": result.get("ocr_used", False),
        "type": pdf_type,
        "text_preview": result["text"][:500] if result["text"] else "",
        "table_summary": [
            {"page": t["page"], "rows": t["row_count"], "cols": len(t.get("headers", []))}
            for t in result.get("tables", [])[:10]
        ],
    }


# ═══════════════════════════════════════════════════════════════
# ВИЗУАЛЬНЫЙ ПРОСМОТР СТРАНИЦ PDF → PNG
# ═══════════════════════════════════════════════════════════════

def render_pdf_pages(data: bytes, pages: list = None, dpi: int = 150) -> dict:
    """Рендерит страницы PDF как PNG изображения."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return {"ok": False, "error": "pip install pdf2image (+ poppler для Windows: https://github.com/oschwartz10612/poppler-windows)"}

    try:
        total = _count_pages(data)
        if pages:
            images_to_render = pages
        else:
            images_to_render = list(range(1, min(total + 1, 11)))  # Макс 10 страниц

        results = []
        for page_num in images_to_render:
            imgs = convert_from_bytes(data, dpi=dpi, first_page=page_num, last_page=page_num, **poppler_options())
            if imgs:
                import time
                fname = f"pdf_page_{page_num}_{int(time.time())}.png"
                path = OUTPUT_DIR / fname
                imgs[0].save(str(path), "PNG")
                results.append({
                    "page": page_num,
                    "filename": fname,
                    "size": path.stat().st_size,
                    "view_url": f"/api/skills/view/{fname}",
                    "download_url": f"/api/skills/download/{fname}",
                })

        return {"ok": True, "pages": results, "total_pages": total, "rendered": len(results)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
