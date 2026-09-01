from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "smoke_agent_endpoints",
    ROOT / "scripts" / "smoke_agent_endpoints.py",
)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_build_checks_covers_configured_service_topology() -> None:
    env = {
        "LLAMA_SERVER_BASE_URL": "http://services:8000/v1",
        "LOCAL_EMBED_BASE_URL": "http://services:8001/v1",
        "OCR_URL": "http://services:8002/ocr",
        "SEARXNG_URL": "http://services:8003",
        "VISION_BASE_URL": "http://services:8004/v1",
        "ELIRA_TTS_URL": "http://services:8005",
        "ELIRA_STT_URL": "http://services:8006",
        "LOCAL_EMBED_DIM": "3",
    }
    with patch.dict(os.environ, env, clear=True):
        checks = smoke.build_checks(skip_generation=False)

    by_service = {check.service: check for check in checks}
    assert set(by_service) == {
        "main-models",
        "main-chat",
        "embedding-models",
        "embedding-vector",
        "ocr-health",
        "searxng-search",
        "vision-models",
        "tts-health",
        "stt-health",
    }
    assert by_service["ocr-health"].url == "http://services:8002/health"
    assert by_service["vision-models"].url == "http://services:8004/v1/models"
    assert by_service["embedding-vector"].expected_embedding_dimension == 1024
    assert by_service["main-chat"].payload["max_tokens"] >= 64


def test_embedding_smoke_fails_closed_on_dimension_mismatch() -> None:
    check = smoke.Check(
        "embedding-vector",
        "POST",
        "http://services:8001/v1/embeddings",
        30,
        expected_embedding_dimension=3,
    )
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "raise_for_status": lambda self: None,
            "json": lambda self: {"data": [{"embedding": [0.1, 0.2]}]},
        },
    )()
    with patch.object(smoke.requests, "request", return_value=response):
        result = smoke._run(check)

    assert result["ok"] is False
    assert result["error"] == "embedding dimension mismatch: expected 3, got 2"


def test_chat_smoke_fails_closed_on_empty_content() -> None:
    check = smoke.Check(
        "main-chat",
        "POST",
        "http://services:8000/v1/chat/completions",
        30,
        require_chat_content=True,
    )
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "raise_for_status": lambda self: None,
            "json": lambda self: {"choices": [{"message": {"content": ""}}]},
        },
    )()
    with patch.object(smoke.requests, "request", return_value=response):
        result = smoke._run(check)

    assert result["ok"] is False
    assert result["error"] == "chat completion returned empty content"
