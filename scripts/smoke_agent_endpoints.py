from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
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
        return {"service": check.service, "ok": True, "status": response.status_code, "url": check.url, "data": data}
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
        return {"service": check.service, "ok": False, "status": 0, "url": check.url, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Elira inference endpoints without changing server state.")
    parser.add_argument("--skip-generation", action="store_true", help="Skip the one-token chat completion probe.")
    args = parser.parse_args()

    llm = os.getenv("LLAMA_SERVER_BASE_URL", "http://192.168.88.15:8000/v1").rstrip("/")
    embed = os.getenv("LOCAL_EMBED_BASE_URL", "http://192.168.88.15:8001/v1").rstrip("/")
    ocr = os.getenv("OCR_SERVICE_URL", "http://192.168.88.15:8002/ocr").rstrip("/")
    vision = os.getenv("VISION_SERVICE_URL", "").rstrip("/")
    model = os.getenv("LLAMA_SERVER_MODEL", "local-model")
    llm_key = os.getenv("LLAMA_SERVER_API_KEY", "local")
    embed_key = os.getenv("LOCAL_EMBED_API_KEY", "local")

    checks = [
        Check("main-models", "GET", f"{llm}/models", 30, api_key=llm_key),
        Check("embedding-models", "GET", f"{embed}/models", 30, api_key=embed_key),
        Check("ocr-health", "GET", ocr.rsplit("/", 1)[0] + "/health", 30),
    ]
    if not args.skip_generation:
        checks.append(Check(
            "main-chat",
            "POST",
            f"{llm}/chat/completions",
            120,
            payload={"model": model, "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 1},
            api_key=llm_key,
        ))
    if vision:
        checks.append(Check("vision-health", "GET", vision.rsplit("/", 1)[0] + "/health", 60))

    results = [_run(check) for check in checks]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
