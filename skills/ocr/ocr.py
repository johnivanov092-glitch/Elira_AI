"""Explicit multipart OCR client for the existing CPU LAN service."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit


def normalize_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("OCR response must be a JSON object")
    result = dict(data)
    raw_pages = data.get("pages", [])
    if not isinstance(raw_pages, list):
        raise ValueError("OCR pages must be a list")
    pages = []
    for index, raw_page in enumerate(raw_pages, 1):
        if isinstance(raw_page, str):
            raw_page = {"text": raw_page}
        if not isinstance(raw_page, dict):
            raise ValueError("OCR page must be an object or text")
        page = dict(raw_page)
        text = page.get("text", page.get("content", ""))
        if not isinstance(text, str):
            raise ValueError("OCR page text must be a string")
        page["text"] = text
        page.setdefault("page", index)
        pages.append(page)
    text = next((data[key] for key in ("text", "full_text", "aggregate_text")
                 if isinstance(data.get(key), str) and data[key].strip()), "")
    if not text:
        text = "\n\n".join(page["text"] for page in pages)
    errors = data.get("errors", [])
    if not isinstance(errors, list):
        raise ValueError("OCR errors must be a list")
    errors = list(errors)
    for page in pages:
        page_errors = page.get("errors", [])
        if not isinstance(page_errors, list):
            raise ValueError("OCR page errors must be a list")
        errors.extend({"page": page["page"], "detail": error} for error in page_errors)
        if not page["text"].strip():
            errors.append({"page": page["page"], "code": "empty_page", "message": "No text; verify the page explicitly"})
    if data.get("complete") is False:
        errors.append({"code": "server_incomplete", "message": "OCR server explicitly marked the result incomplete"})
    if data.get("ok") is False:
        errors.append({"code": "server_failure", "message": "OCR server explicitly reported failure"})
    if not text.strip():
        errors.append({"code": "empty_text", "message": "OCR returned no text"})
    complete = bool(text.strip()) and not errors
    result.update(text=text, pages=pages, errors=errors, ok=data.get("ok") is not False, complete=complete)
    return result


def request_ocr(path: Path, url: str, language: str, timeout: float, pdf_fallback: bool) -> dict[str, Any]:
    import requests
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OCR URL must be an explicit HTTP(S) endpoint without embedded credentials")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        response = requests.post(url, files={"file": (path.name, handle, mime)},
                                 data={"language": language, "pdf_fallback": str(pdf_fallback).lower()},
                                 timeout=timeout, allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError("OCR endpoint redirected; provide the intended endpoint explicitly")
    response.raise_for_status()
    return normalize_response(response.json())


def request_pdf_ocr(path: Path, url: str, language: str, timeout: float,
                    pdf_fallback: bool = True) -> dict[str, Any]:
    """Upload the original PDF; rendering and recognition run on the LAN server."""
    result = request_ocr(path, url, language, timeout, pdf_fallback)
    pages = result["pages"]
    declared_count = result.get("total_pages", result.get("page_count", len(pages)))
    page_ids = [page["page"] for page in pages]
    if (not pages or isinstance(declared_count, bool) or not isinstance(declared_count, int)
            or declared_count != len(pages)
            or any(isinstance(number, bool) or not isinstance(number, int) for number in page_ids)
            or sorted(page_ids) != list(range(1, len(pages) + 1))):
        result["errors"].append({"code": "incomplete_pdf_pages",
                                 "message": "Server PDF pages are missing or do not match its declared page count"})
        result["complete"] = False
    result.update(source=str(path), pdf_mode="server", total_pages=declared_count,
                  requested_pages=None, whole_document_complete=result["complete"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--url", default=os.environ.get("OCR_URL", ""))
    parser.add_argument("--language", choices=("auto", "ru", "en"), default="auto")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--no-pdf-fallback", action="store_true")
    parser.add_argument("--pdf-mode", choices=("server",), default="server",
                         help="PDF rendering and recognition run on the LAN server")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=20000)
    args = parser.parse_args()
    try:
        if args.timeout <= 0 or args.offset < 0 or args.max_chars < 1:
            raise ValueError("timeout/max-chars must be positive; offset must be nonnegative")
        path = args.input.expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Input must be a file")
        if any(output and output.resolve() == path for output in (args.output, args.json_output)):
            raise ValueError("Output must not overwrite the input")
        if args.output and args.json_output and args.output.resolve() == args.json_output.resolve():
            raise ValueError("Text and JSON outputs must use different paths")
        if path.suffix.lower() == ".pdf":
            result = request_pdf_ocr(path, args.url, args.language, args.timeout, not args.no_pdf_fallback)
        else:
            result = request_ocr(path, args.url, args.language, args.timeout, not args.no_pdf_fallback)
        if args.output:
            args.output.write_text(result["text"], encoding="utf-8", newline="\n")
        if args.json_output:
            args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        end, text = args.offset + args.max_chars, result["text"]
        print(json.dumps({"ok": result["ok"], "complete": result["complete"], "errors": result["errors"],
                          "text": text[args.offset:end], "total_chars": len(text), "page_count": len(result["pages"]),
                          "truncated": end < len(text), "next_offset": end if end < len(text) else None,
                          "pdf_mode": result.get("pdf_mode"), "total_pages": result.get("total_pages"),
                          "requested_pages": result.get("requested_pages"),
                          "whole_document_complete": result.get("whole_document_complete", result["complete"]),
                          "output": str(args.output) if args.output else None,
                          "json_output": str(args.json_output) if args.json_output else None}, ensure_ascii=False))
        return 0 if result["complete"] else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
