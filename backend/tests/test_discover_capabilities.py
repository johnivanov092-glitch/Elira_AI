"""Regression tests for configured vision/OCR capabilities."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.run_journal import discover_capabilities  # noqa: E402
from app.infrastructure.llm.vision_ocr import ocr_config, vision_config  # noqa: E402


def test_vision_and_server_ocr_are_configured_without_feature_flags() -> None:
    with patch.dict(
        os.environ,
        {"VISION_ENABLED": "false", "OCR_ENABLED": "false"},
        clear=False,
    ):
        caps = discover_capabilities(model="m", tools=[])

    assert not hasattr(vision_config(), "enabled")
    assert not hasattr(ocr_config(), "enabled")
    assert caps["vision"]["available"] is True
    assert caps["ocr"]["available"] is True
    assert caps["ocr"]["provider"] == "server-ocr"
    assert "vision" not in caps["missing"]
    assert "ocr" not in caps["missing"]


def test_configured_capabilities_do_not_probe_network() -> None:
    with patch("requests.get") as get, patch("requests.post") as post:
        caps = discover_capabilities(model="m", tools=[])

    assert caps["vision"]["available"] is True
    assert caps["ocr"]["available"] is True
    get.assert_not_called()
    post.assert_not_called()
