from __future__ import annotations

import re

from app.core.persona_defaults import (
    AUTO_PROFILE,
    DEFAULT_PROFILE,
    LEGACY_PROFILE_TO_MODE,
    PERSONA_MODES,
)


def normalize_profile(name: str) -> str:
    """Resolve a stored/requested name to a real persona mode.

    Accepts a current mode, a legacy profile name ("Программист" -> "Инженерный"),
    or "default"/empty -> DEFAULT_PROFILE. "Авто" is NOT resolved here (it needs
    the message text — see resolve_persona_mode)."""
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    if name in PERSONA_MODES:
        return name
    return LEGACY_PROFILE_TO_MODE.get(name, DEFAULT_PROFILE)


def resolve_profile_name(name: str | None) -> str:
    """Pick the effective persona mode for a request WITHOUT message context.

    Used by background paths (autopipeline) that have no live user message to
    classify. An explicit mode is honored; "default"/empty falls back to the
    saved `agent_profile`; "Авто" (no text to classify here) resolves to the
    neutral DEFAULT_PROFILE so background runs stay deterministic.
    """
    if name and name.lower() != "default":
        if name == AUTO_PROFILE:
            return DEFAULT_PROFILE
        return normalize_profile(name)
    # Lazy import: settings pulls in storage and would create an import cycle
    # if loaded at module top alongside the persona machinery.
    from app.application.elira_memory.settings import get_settings

    stored = ""
    try:
        stored = str(get_settings().get("agent_profile") or "")
    except Exception:
        stored = ""
    if stored == AUTO_PROFILE:
        return DEFAULT_PROFILE
    return normalize_profile(stored)


# ── Auto-mode heuristic (Living Persona, step A) ─────────────────────────────
# When the picker is on "Авто", classify each message into a mode by simple,
# fast signals — no extra model call. Engineering signals win first (so a code
# request always reaches its tools), then personal/companion signals, else the
# neutral Баланс.
_CODE_SIGNALS = re.compile(
    r"(?:\b(?:баг|ошибк|исключени|traceback|стек\s*трейс|функци|класс|метод|"
    r"рефактор|патч|коммит|деплой|компил|собери|собрать|запусти|запуск|"
    r"тест|линт|дебаг|отлад|почини|исправ|перепиши|напиши\s+код|реализуй|"
    r"имплемент|merge|pull\s*request|pr\b|api\b|endpoint|роут|миграци|"
    r"bug|fix|refactor|implement|deploy|compile|build|debug|stack\s*trace|"
    r"exception|commit|function|class\b)"
    r"|\.(?:py|ts|tsx|js|jsx|go|rs|java|c|cpp|h|sql|json|yaml|yml|sh|toml)\b"
    r"|`[^`]+`)",
    re.IGNORECASE,
)
_PERSONAL_SIGNALS = re.compile(
    r"\b(?:устал|устала|грустно|тяжело|одиноко|спасибо|как\s+(?:ты|дела|сама)|"
    r"поговор|посоветуй\s+по\s+жизни|переживаю|тревож|настроени|скуч|"
    r"люблю|нравишься|поддержи|обними|расскажи\s+о\s+себе)\b",
    re.IGNORECASE,
)


def classify_mode(user_input: str | None) -> str:
    """Heuristic mode for "Авто": Инженерный / Личный / Баланс."""
    text = (user_input or "").strip()
    if not text:
        return DEFAULT_PROFILE
    if _CODE_SIGNALS.search(text):
        return "Инженерный"
    if _PERSONAL_SIGNALS.search(text):
        return "Личный"
    return DEFAULT_PROFILE


def resolve_persona_mode(requested: str | None, user_input: str | None) -> str:
    """Resolve the effective mode for a live request (hybrid Авто + lock).

    Priority: an explicit per-request mode wins (lock); else the saved
    `agent_profile`; if that is "Авто" (or unset), classify the message.
    """
    req = (requested or "").strip()
    if not req or req.lower() == "default":
        from app.application.elira_memory.settings import get_settings

        try:
            req = str(get_settings().get("agent_profile") or AUTO_PROFILE)
        except Exception:
            req = AUTO_PROFILE
    if req == AUTO_PROFILE or req.lower() == "auto":
        return classify_mode(user_input)
    return normalize_profile(req)
