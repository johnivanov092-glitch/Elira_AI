"""Regression tests for discover_capabilities (run_journal).

The old implementation hardcoded vision `available=False` and probed only a
local `tesseract` binary for OCR, so a fully-working server vision (:8004) /
OCR (:8002) setup was falsely reported as a "missing" capability. These tests
pin the truthful behaviour: vision follows VISION_ENABLED, OCR is available via
the server (OCR_ENABLED) OR a local tesseract fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.run_journal import discover_capabilities  # noqa: E402

VJ = "app.infrastructure.llm.vision_ocr"
WHICH = "app.application.code_agent.run_journal.shutil.which"


def test_vision_and_server_ocr_reported_available_when_enabled() -> None:
    with patch(f"{VJ}.is_vision_enabled", return_value=True), \
         patch(f"{VJ}.is_ocr_enabled", return_value=True):
        caps = discover_capabilities(model="m", tools=["web_search", "web_fetch"])

    assert caps["vision"]["available"] is True
    assert "vision" not in caps["missing"]
    assert caps["ocr"]["available"] is True
    assert caps["ocr"]["provider"] == "server-ocr"
    assert "ocr" not in caps["missing"]


def test_vision_and_ocr_missing_when_disabled_and_no_tesseract() -> None:
    with patch(f"{VJ}.is_vision_enabled", return_value=False), \
         patch(f"{VJ}.is_ocr_enabled", return_value=False), \
         patch(WHICH, return_value=None), \
         patch("app.application.pdf.runtime._TESSERACT_CANDIDATES", []):
        caps = discover_capabilities(model="m", tools=[])

    assert caps["vision"]["available"] is False
    assert "vision" in caps["missing"]
    assert caps["ocr"]["available"] is False
    assert "ocr" in caps["missing"]


def test_ocr_falls_back_to_local_tesseract_when_server_off() -> None:
    with patch(f"{VJ}.is_vision_enabled", return_value=False), \
         patch(f"{VJ}.is_ocr_enabled", return_value=False), \
         patch(WHICH, return_value="/usr/bin/tesseract"):
        caps = discover_capabilities(model="m", tools=[])

    assert caps["ocr"]["available"] is True
    assert caps["ocr"]["provider"] == "tesseract"
    assert "ocr" not in caps["missing"]


def test_vision_not_hardcoded_false_regression() -> None:
    """The specific bug: vision must reflect the flag, never a hardcoded False."""
    with patch(f"{VJ}.is_vision_enabled", return_value=True), \
         patch(f"{VJ}.is_ocr_enabled", return_value=False), \
         patch(WHICH, return_value=None), \
         patch("app.application.pdf.runtime._TESSERACT_CANDIDATES", []):
        caps = discover_capabilities(model="m", tools=[])

    assert caps["vision"]["available"] is True
    assert caps["vision"].get("reason") is None
