from __future__ import annotations

import re
from typing import Any


IDENTITY_QUESTION_RE = re.compile(
    r"(кто\s+ты|как\s+тебя\s+зовут|представься|ты\s+кто|что\s+ты\s+за|назови\s+себя)",
    re.IGNORECASE,
)

MODEL_IDENTITY_RE = re.compile(
    r"(?i)\b("
    r"gemma|qwen|llama|deepseek|mistral|nemotron|gpt|yandexgpt|google\s+deepmind|"
    r"large\s+language\s+model|llm|языковая\s+модель|большая\s+языковая\s+модель"
    r")\b"
)

FIRST_PERSON_RE = re.compile(r"(?i)\b(я|меня|мне|мой|моя|моё|мои)\b")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _safe_identity_reply(persona_name: str) -> str:
    return (
        f"Меня зовут {persona_name}. Я твоя AI-ассистентка в Elira AI: "
        "помогаю с задачами, кодом, анализом, исследованиями и рабочими процессами."
    )


def is_identity_question(user_input: str) -> bool:
    return bool(IDENTITY_QUESTION_RE.search(user_input or ""))


def _contains_model_identity(sentence: str) -> bool:
    text = (sentence or "").strip()
    if not text:
        return False
    return bool(MODEL_IDENTITY_RE.search(text) and FIRST_PERSON_RE.search(text))


def _rewrite_identity_drift(answer_text: str, persona_name: str) -> str:
    sentences = SENTENCE_SPLIT_RE.split((answer_text or "").strip())
    kept: list[str] = []
    removed_any = False
    for sentence in sentences:
        if _contains_model_identity(sentence):
            removed_any = True
            continue
        kept.append(sentence.strip())

    rewritten = " ".join(part for part in kept if part).strip()
    if not rewritten:
        return _safe_identity_reply(persona_name)
    if removed_any and persona_name.lower() not in rewritten.lower():
        return f"{_safe_identity_reply(persona_name)} {rewritten}".strip()
    return rewritten


def _still_drifting(answer_text: str, persona_name: str) -> bool:
    text = (answer_text or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if f"меня зовут {persona_name.lower()}" in lowered:
        cleaned = re.sub(fr"(?i)меня\s+зовут\s+{re.escape(persona_name)}", "", lowered)
    else:
        cleaned = lowered
    return bool(_contains_model_identity(cleaned))


def guard_identity_response(user_input: str, answer_text: str, persona_name: str = "Elira") -> dict[str, Any]:
    original = (answer_text or "").strip()
    if not original:
        return {"text": original, "changed": False, "reason": None, "identity_question": is_identity_question(user_input)}

    identity_question = is_identity_question(user_input)
    if identity_question:
        safe = _safe_identity_reply(persona_name)
        if original == safe:
            return {"text": safe, "changed": False, "reason": None, "identity_question": True}
        return {"text": safe, "changed": True, "reason": "identity_question_locked", "identity_question": True}

    if not _contains_model_identity(original):
        return {"text": original, "changed": False, "reason": None, "identity_question": False}

    rewritten = _rewrite_identity_drift(original, persona_name)
    if _still_drifting(rewritten, persona_name):
        rewritten = _safe_identity_reply(persona_name)
        reason = "identity_fragment_replaced"
    else:
        reason = "identity_rewritten"

    return {
        "text": rewritten,
        "changed": rewritten != original,
        "reason": reason,
        "identity_question": False,
    }
