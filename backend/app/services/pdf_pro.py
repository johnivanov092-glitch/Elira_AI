"""Thin facade — all PDF processing logic lives in infrastructure/files/pdf_pro.py."""
from app.infrastructure.files.pdf_pro import (  # noqa: F401
    OUTPUT_DIR,
    _count_pages,
    _try_ocr,
    _try_pdfplumber,
    _try_pypdf,
    analyze_pdf,
    extract_pdf_smart,
    pdf_tables_to_excel,
    pdf_to_word,
    render_pdf_pages,
)
