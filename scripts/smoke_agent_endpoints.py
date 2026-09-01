from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class Check:
    service: str
    method: str
    url: str
    timeout: float
    payload: dict[str, Any] | None = None
    api_key: str = ""
    expected_embedding_dimension: int | None = None


ROOT = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    """Load the same backend env files as app.main without overriding the shell."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    backend = ROOT / "backend"
    load_dotenv(backend / ".env", override=False)
    load_dotenv(backend / ".env.local", override=False)


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _run(check: Check) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if check.api_key:
        headers["Authorization"] = f"Bearer {check.api_key}"
    try:
        response = requests.request(
            check.method,
            check.url,
            headers=headers,
            json=check.payload,
            timeout=check.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if check.expected_embedding_dimension is not None:
            rows = data.get("data") if isinstance(data, dict) else None
            vector = rows[0].get("embedding") if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
            actual = len(vector) if isinstance(vector, list) else 0
            if actual != check.expected_embedding_dimension:
                return {
                    "service": check.service,
                    "ok": False,
                    "status": response.status_code,
                    "url": check.url,
                    "error": (
                        "embedding dimension mismatch: expected "
                        f"{check.expected_embedding_dimension}, got {actual}"
                    ),
                }
        return {"service": check.service, "ok": True, "status": response.status_code, "url": check.url}
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        return {
            "service": check.service,
            "ok": False,
            "status": status,
            "url": check.url,
            "error": f"HTTP {status}; verify this service base URL and path",
        }
    except (requests.RequestException, ValueError) as exc:
        return {
            "service": check.service,
            "ok": False,
            "status": 0,
            "url": check.url,
            "error": f"{type(exc).__name__}: endpoint unavailable or returned invalid JSON",
        }


def build_checks(*, skip_generation: bool) -> list[Check]:
    llm = os.getenv("LLAMA_SERVER_BASE_URL", "http://192.168.88.15:8000/v1").rstrip("/")
    embed = os.getenv("LOCAL_EMBED_BASE_URL", "http://192.168.88.15:8001/v1").rstrip("/")
    ocr = os.getenv("OCR_URL", "http://192.168.88.15:8002/ocr").rstrip("/")
    searxng = os.getenv("SEARXNG_URL", "http://192.168.88.15:8003").rstrip("/")
    vision = os.getenv("VISION_BASE_URL", "http://192.168.88.15:8004/v1").rstrip("/")
    tts = os.getenv("ELIRA_TTS_URL", "http://192.168.88.15:8005").rstrip("/")
    stt = os.getenv("ELIRA_STT_URL", "http://192.168.88.15:8006").rstrip("/")
    model = os.getenv("LLAMA_SERVER_MODEL", "local-model")
    embed_model = os.getenv("LOCAL_EMBED_MODEL", "local-embed")
    llm_key = os.getenv("LLAMA_SERVER_API_KEY", "local")
    embed_key = os.getenv("LOCAL_EMBED_API_KEY", "local")
    embed_dim = _positive_int("LOCAL_EMBED_DIM", 1024)

    checks = [
        Check("main-models", "GET", f"{llm}/models", 30, api_key=llm_key),
        Check("embedding-models", "GET", f"{embed}/models", 30, api_key=embed_key),
        Check(
            "embedding-vector",
            "POST",
            f"{embed}/embeddings",
            60,
            payload={"model": embed_model, "input": "Elira smoke"},
            api_key=embed_key,
            expected_embedding_dimension=embed_dim,
        ),
        Check("ocr-health", "GET", ocr.rsplit("/", 1)[0] + "/health", 30),
        Check(
            "searxng-search",
            "GET",
            f"{searxng}/search?q=Elira+smoke&format=json",
            30,
        ),
        Check("vision-models", "GET", f"{vision}/models", 60),
        Check("tts-health", "GET", f"{tts}/health", 30),
        Check("stt-health", "GET", f"{stt}/health", 30),
    ]
    if not skip_generation:
        checks.append(Check(
            "main-chat",
            "POST",
            f"{llm}/chat/completions",
            180,
            payload={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 64,
                "temperature": 0,
            },
            api_key=llm_key,
        ))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Elira inference endpoints without changing server state.")
    parser.add_argument("--skip-generation", action="store_true", help="Skip the chat completion probe.")
    args = parser.parse_args()
    load_environment()
    results = [_run(check) for check in build_checks(skip_generation=args.skip_generation)]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
