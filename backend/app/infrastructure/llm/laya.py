"""CPU Laya request hints; failures leave the main agent in control."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
import math
import os
from time import monotonic
from urllib.parse import urlsplit

import requests


logger = logging.getLogger(__name__)
CONTEXT_WINDOW = 8192
MODEL_CHECKPOINT = "convaiinnovations/laya/multilingual"
MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
_MAX_RESPONSE_BYTES = 64 * 1024
TASK_CRITERIA = {
    "transcription": "Расшифровка аудио, обработка изображения или видео",
    "code": "Создание или изменение кода и программ",
    "web": "Поиск актуальной информации в интернете",
    "document": "Создание документа или таблицы",
    "other": "Другая задача, объяснение или обычный разговор",
}
TASK_HINTS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "code": ("Инженерный", ("project",)),
    "transcription": (None, ("resources", "project")),
    "document": ("Деловой", ("resources", "data")),
    "web": (None, ("web",)),
    "other": (None, ()),
}


def request_questions() -> dict[str, dict[str, object]]:
    """Concrete alternatives; generic yes/no schemas failed live RU evaluation."""
    return {"main_task": {
        "type": "choice",
        "instructions": "Определи основную задачу пользователя.",
        "criteria": dict(TASK_CRITERIA),
    }}


@dataclass(frozen=True)
class LayaDecision:
    domains: tuple[str, ...] = ()
    capability_groups: frozenset[str] = frozenset()
    source: str = "main_agent"
    error: str = ""
    elapsed_ms: float = 0.0
    confidence: dict[str, float] = field(default_factory=dict)
    uncertain: tuple[str, ...] = ()
    runtime: dict[str, object] = field(default_factory=dict)

    def diagnostics(self) -> dict[str, object]:
        """Only bounded metadata; never include user text or HTTP error bodies."""
        return {
            "source": self.source,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "confidence": dict(self.confidence),
            "uncertain": list(self.uncertain),
            "runtime": dict(self.runtime),
        }


def _fallback(error: str, started: float) -> LayaDecision:
    elapsed_ms = round((monotonic() - started) * 1000, 3)
    if error not in {"disabled", "empty_input"}:
        logger.warning("laya_preflight_fallback error=%s elapsed_ms=%.3f", error, elapsed_ms)
    return LayaDecision(error=error, elapsed_ms=elapsed_ms)


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_probability")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("invalid_probability")
    return number


def classify_request(
    text: str,
    *,
    domains: Mapping[str, str],
    capabilities: Mapping[str, str],
) -> LayaDecision:
    """Classify the complete current request into optional primary-task hints.

    The service rejects token overflow instead of truncating. Other tasks
    receive no hint; confidence is diagnostic, never an approval gate.
    Mixed tasks retain their complete original request for the main agent;
    this single choice neither constrains nor exhaustively plans their work.
    """
    started = monotonic()
    if os.getenv("LAYA_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return _fallback("disabled", started)
    if not text.strip():
        return _fallback("empty_input", started)
    base_url = os.getenv("LAYA_BASE_URL", "http://192.168.88.15:8008").rstrip("/")
    try:
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise ValueError("invalid_url")
        timeout = float(os.getenv("LAYA_TIMEOUT_SECONDS", "15"))
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("invalid_timeout")
    except ValueError:
        return _fallback("invalid_configuration", started)

    questions = request_questions()

    try:
        with requests.post(
            f"{base_url}/v1/systemone",
            json={"state": text, "questions": questions, "max_len": CONTEXT_WINDOW},
            timeout=(min(3.0, timeout), timeout),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code != 200:
                return _fallback(f"http_{response.status_code}", started)
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=8192):
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    return _fallback("response_too_large", started)
                if monotonic() - started > timeout:
                    return _fallback("timeout", started)
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
    except requests.Timeout:
        return _fallback("timeout", started)
    except requests.RequestException:
        return _fallback("transport_error", started)
    except (ValueError, UnicodeError):
        return _fallback("invalid_response", started)

    try:
        if not isinstance(payload, dict):
            raise ValueError("invalid_response")
        runtime = payload.get("runtime")
        if not isinstance(runtime, dict) or (
            runtime.get("device") != "cpu"
            or runtime.get("checkpoint") != MODEL_CHECKPOINT
            or runtime.get("threads") != 6
            or runtime.get("context_window") != CONTEXT_WINDOW
        ):
            raise ValueError("runtime_mismatch")
        revision = runtime.get("revision")
        if not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("invalid_revision")
        if revision != MODEL_REVISION:
            raise ValueError("revision_mismatch")
        sequences = runtime.get("sequence_tokens")
        if not isinstance(sequences, dict) or set(sequences) != set(questions):
            raise ValueError("invalid_token_counts")
        if any(type(n) is not int or not 0 < n <= CONTEXT_WINDOW for n in sequences.values()):
            raise ValueError("invalid_token_counts")
        answers = payload.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("invalid_answers")
        answer = answers["main_task"]
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ValueError("invalid_answer")
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in TASK_HINTS:
            raise ValueError("invalid_choice")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(TASK_CRITERIA):
            raise ValueError("invalid_probabilities")
        if not math.isclose(sum(_probability(p) for p in probabilities.values()), 1.0, abs_tol=1e-3):
            raise ValueError("invalid_probabilities")
        confidence = {"main_task": _probability(answer.get("answer_confidence"))}
        domain, groups = TASK_HINTS[choice]
        return LayaDecision(
            domains=(domain,) if domain in domains else (),
            capability_groups=frozenset(group for group in groups if group in capabilities),
            source="laya",
            elapsed_ms=round((monotonic() - started) * 1000, 3),
            confidence=confidence,
            uncertain=("main_task",) if choice == "other" else (),
            runtime={
                "device": "cpu", "checkpoint": MODEL_CHECKPOINT, "threads": 6,
                "context_window": CONTEXT_WINDOW, "revision": revision,
            },
        )
    except (TypeError, ValueError):
        return _fallback("invalid_response", started)
