"""Living Persona step B — Elira's mood (transient emotional coloring).

Mood is a GLOBAL, two-axis state (energy + warmth, each in [0,1]) that drifts
automatically from the conversation and decays back toward a slightly-warm
baseline over time. It NEVER changes identity, honesty or the disallowed-drift
bounds — it only colors tone (a single line appended to the persona voice).

Stored in its own single-row table, separate from the durable persona versions:
mood is ephemeral runtime state, not part of who Elira is.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.application.persona import store as persona_store

logger = logging.getLogger(__name__)

# Baseline = resting mood. Warmth sits slightly above the midpoint because
# Elira's core voice is warm; energy rests neutral.
BASELINE = {"energy": 0.5, "warmth": 0.55}
_WARMTH_FLOOR = 0.3            # never colder than this -> no coldness/rudeness
_LO, _HI = 0.05, 0.95
_DECAY_HALFLIFE_MIN = 45.0    # minutes to halve the distance back to baseline
_STEP = 0.06                  # per-turn nudge magnitude

_WARM_RE = re.compile(
    r"(спасибо|благодар|тепл|обним|рад\b|приятно|нравит|люблю|поддерж|классно|"
    r"здорово|молодец|выручил|ценю|добр)",
    re.IGNORECASE,
)
_EXCITED_RE = re.compile(
    r"(!|ура|круто|супер|отлично|давай|погнали|вперёд|огонь|ого\b|вау)",
    re.IGNORECASE,
)
_FRUSTRATION_RE = re.compile(
    r"(баг|ошибк|не работает|не получается|устал|бьюсь|опять|сломал|fail|"
    r"traceback|капец|бесит|задолбал|злюсь|раздража|ничего не выходит|тяжело)",
    re.IGNORECASE,
)


def _clamp(value: float, lo: float = _LO, hi: float = _HI) -> float:
    return max(lo, min(hi, value))


def _ensure_table() -> None:
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_mood (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                energy REAL NOT NULL,
                warmth REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _read_row():
    _ensure_table()
    conn = persona_store.connect()
    try:
        return conn.execute(
            "SELECT energy, warmth, updated_at FROM persona_mood WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()


def _decayed(energy: float, warmth: float, updated_at: str) -> tuple[float, float]:
    """Pull (energy, warmth) toward baseline by elapsed time (half-life)."""
    try:
        ts = datetime.fromisoformat(updated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    except Exception:
        elapsed_min = 0.0
    factor = 0.5 ** (max(0.0, elapsed_min) / _DECAY_HALFLIFE_MIN)
    e = BASELINE["energy"] + (energy - BASELINE["energy"]) * factor
    w = BASELINE["warmth"] + (warmth - BASELINE["warmth"]) * factor
    return _clamp(e), _clamp(max(_WARMTH_FLOOR, w))


def mood_label(energy: float, warmth: float) -> str:
    hi_e, lo_e = energy >= 0.6, energy <= 0.4
    hi_w, lo_w = warmth >= 0.62, warmth < 0.48
    if hi_e and hi_w:
        return "тёплая и бодрая"
    if hi_e and lo_w:
        return "энергичная, собранная"
    if lo_e and hi_w:
        return "тихая и тёплая"
    if lo_e and lo_w:
        return "тихая, сосредоточенная"
    if lo_e:
        return "спокойная"
    if hi_e:
        return "оживлённая"
    return "ровная"


def get_mood() -> dict:
    """Current mood with time-decay applied. Fail-safe: baseline on any error."""
    try:
        row = _read_row()
        if not row:
            e, w = BASELINE["energy"], BASELINE["warmth"]
        else:
            e, w = _decayed(float(row["energy"]), float(row["warmth"]), row["updated_at"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("get_mood failed, using baseline: %s", exc)
        e, w = BASELINE["energy"], BASELINE["warmth"]
    return {"energy": round(e, 3), "warmth": round(w, 3), "label": mood_label(e, w)}


def mood_overlay_line(mood: dict | None = None) -> str:
    """The single voice-coloring line appended to the persona prompt."""
    mood = mood or get_mood()
    return (
        f"Сейчас твоё состояние: {mood['label']}. "
        "Пусть оно мягко окрашивает тон — не меняя сути, честности и правил."
    )


def _signal_deltas(text: str) -> tuple[float, float]:
    t = (text or "").lower()
    de = dw = 0.0
    if _WARM_RE.search(t):
        dw += _STEP
        de += _STEP * 0.5
    if _EXCITED_RE.search(t):
        de += _STEP
    if _FRUSTRATION_RE.search(t):
        de -= _STEP
        dw -= _STEP * 0.4
    return de, dw


def nudge_mood(user_text: str = "", assistant_text: str = "") -> dict:
    """Drift the mood from one exchange. The user's message weighs full; the
    assistant's own text weighs half. Fail-safe: never raises into the loop."""
    try:
        row = _read_row()
        if row:
            e, w = _decayed(float(row["energy"]), float(row["warmth"]), row["updated_at"])
        else:
            e, w = BASELINE["energy"], BASELINE["warmth"]
        due, dwu = _signal_deltas(user_text)
        dea, dwa = _signal_deltas(assistant_text)
        e = _clamp(e + due + dea * 0.5)
        w = _clamp(max(_WARMTH_FLOOR, w + dwu + dwa * 0.5))
        conn = persona_store.connect()
        try:
            conn.execute(
                """
                INSERT INTO persona_mood(id, energy, warmth, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    energy = excluded.energy,
                    warmth = excluded.warmth,
                    updated_at = excluded.updated_at
                """,
                (e, w, persona_store.utc_now()),
            )
            conn.commit()
        finally:
            conn.close()
        return {"energy": round(e, 3), "warmth": round(w, 3), "label": mood_label(e, w)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("nudge_mood failed: %s", exc)
        return get_mood()
