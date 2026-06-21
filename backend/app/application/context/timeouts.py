from __future__ import annotations


TIMEOUT_POLICY_SECONDS = {
    "chat": 120,
    "code": 600,
    "project_analysis": 600,
    "long_context_128k": 600,
    "long_context_192k": 900,
    "long_context_256k": 900,
    "embedding": 30,
    "ocr": 60,
    "vision": 120,
}


def timeout_for_task(task_type: str, *, ctx_size: int = 131_072) -> int:
    kind = str(task_type or "chat").strip().lower()
    if kind == "long_context":
        if ctx_size >= 262_144:
            return TIMEOUT_POLICY_SECONDS["long_context_256k"]
        if ctx_size >= 196_608:
            return TIMEOUT_POLICY_SECONDS["long_context_192k"]
        return TIMEOUT_POLICY_SECONDS["long_context_128k"]
    return TIMEOUT_POLICY_SECONDS.get(kind, TIMEOUT_POLICY_SECONDS["chat"])
