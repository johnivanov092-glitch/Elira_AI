"""Agent routing and strategy selection.

Extracted from core/agents.py — task routing heuristics and
V8 strategy selection with learned preferences.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Task-graph templates per mode
# ---------------------------------------------------------------------------

TASK_GRAPH_TEMPLATES_V8 = {
    "chat": ["retrieve_memory", "finalize"],
    "research": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "code": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "file": ["retrieve_memory", "retrieve_kb", "tool_hint", "task_graph", "reflection_v2", "finalize"],
    "multi_step": ["retrieve_memory", "retrieve_kb", "tool_hint", "planner", "reflection_v2", "finalize"],
}


# ---------------------------------------------------------------------------
# Keyword-based task router
# ---------------------------------------------------------------------------

_FILE_MARKERS = [
    "pdf", "docx", "txt", "csv", "excel", "xlsx",
    "\u0444\u0430\u0439\u043b", "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442",
    "\u0442\u0430\u0431\u043b\u0438\u0446\u0430",
]

_RESEARCH_MARKERS = [
    "\u043d\u0430\u0439\u0434\u0438", "\u043f\u043e\u0438\u0449\u0438",
    "\u0438\u0441\u0441\u043b\u0435\u0434\u0443\u0439", "research", "browser", "web",
    "\u0432\u0435\u0431", "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446",
    "\u0441\u0430\u0439\u0442",
]

_CODE_MARKERS = [
    "python", "\u043a\u043e\u0434", "fastapi", "api", "streamlit", "bug",
    "\u043e\u0448\u0438\u0431\u043a\u0430", "\u0440\u0435\u0444\u0430\u043a\u0442\u043e\u0440",
    "\u0441\u043a\u0440\u0438\u043f\u0442",
]

_PLANNER_MARKERS = [
    "\u043f\u043b\u0430\u043d", "\u043f\u043e \u0448\u0430\u0433\u0430\u043c",
    "\u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442\u0443\u0440", "pipeline",
    "\u0441\u0442\u0440\u0430\u0442\u0435\u0433", "roadmap",
]


def route_task(
    user_text: str,
    model_name: str = "",
    memory_profile: str = "",
    num_ctx: int = 4096,
) -> dict[str, Any]:
    """Keyword-based router returning mode / agent / use_graph / confidence."""
    t = (user_text or "").lower()

    if any(x in t for x in _FILE_MARKERS):
        return {"mode": "file", "agent": "file_agent", "use_graph": True,
                "confidence": 0.86, "source": "keyword", "reason": "file markers"}

    if any(x in t for x in _RESEARCH_MARKERS):
        return {"mode": "research", "agent": "browser_agent", "use_graph": True,
                "confidence": 0.84, "source": "keyword", "reason": "research markers"}

    if any(x in t for x in _CODE_MARKERS):
        return {"mode": "code", "agent": "coder_agent", "use_graph": True,
                "confidence": 0.85, "source": "keyword", "reason": "code markers"}

    if any(x in t for x in _PLANNER_MARKERS):
        return {"mode": "multi_step", "agent": "planner_agent", "use_graph": True,
                "confidence": 0.78, "source": "keyword", "reason": "planner markers"}

    return {"mode": "chat", "agent": "chat_agent", "use_graph": False,
            "confidence": 0.55, "source": "fallback", "reason": "default chat"}


# ---------------------------------------------------------------------------
# V8 strategy selector (with learned preferences)
# ---------------------------------------------------------------------------

def choose_v8_strategy(
    task: str,
    route: dict[str, Any],
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    force_strategy: str | None = None,
) -> dict[str, Any]:
    """Select an execution strategy based on task, route, and learned history."""
    from app.domain.memory.strategy_tracking import get_v8_strategy_preferences

    if force_strategy:
        return {
            "strategy": str(force_strategy),
            "confidence": 1.0,
            "source": "forced",
            "reason": "force_strategy parameter",
            "scores": {str(force_strategy): 1.0},
            "learned_preferences": [],
        }

    mode = str((route or {}).get("mode", "chat") or "chat")
    text = (task or "").lower()

    scores: dict[str, float] = {
        "direct": 0.20,
        "planner": 0.20,
        "task_graph": 0.20,
        "multi_agent": 0.20,
        "self_improve": 0.10,
    }

    mode_bias: dict[str, dict[str, float]] = {
        "chat": {"direct": 1.30},
        "research": {"task_graph": 1.05, "planner": 0.35},
        "code": {"task_graph": 0.90, "multi_agent": 0.70},
        "file": {"task_graph": 0.90, "planner": 0.40},
        "multi_step": {"planner": 1.10, "multi_agent": 0.55},
    }
    for k, v in mode_bias.get(mode, {}).items():
        scores[k] = scores.get(k, 0.0) + v

    long_task = len(task or "") > 280
    if long_task:
        scores["multi_agent"] += 0.35
        scores["task_graph"] += 0.25

    _DIRECT_KW = [
        "\u0447\u0442\u043e \u0442\u0430\u043a\u043e\u0435",
        "\u043e\u0431\u044a\u044f\u0441\u043d\u0438",
        "\u043a\u0440\u0430\u0442\u043a\u043e",
        "\u043f\u0440\u043e\u0441\u0442\u044b\u043c\u0438 \u0441\u043b\u043e\u0432\u0430\u043c\u0438",
        "short", "summary",
    ]
    _PLANNER_KW = [
        "\u043f\u043b\u0430\u043d", "roadmap",
        "\u043f\u043e \u0448\u0430\u0433\u0430\u043c",
        "\u0448\u0430\u0433\u0438 \u0432\u043d\u0435\u0434\u0440\u0435\u043d\u0438\u044f",
        "\u0441\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u044f \u0432\u043d\u0435\u0434\u0440\u0435\u043d\u0438\u044f",
    ]
    _RESEARCH_KW = [
        "\u0438\u0441\u0441\u043b\u0435\u0434\u0443\u0439",
        "\u0441\u0440\u0430\u0432\u043d\u0438",
        "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446",
        "\u043f\u0440\u0430\u043a\u0442\u0438\u043a\u0438",
        "best practices",
        "\u043d\u0430\u0439\u0434\u0438",
        "langgraph", "crewai",
    ]
    _MULTI_AGENT_KW = [
        "\u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442\u0443\u0440",
        "\u0440\u0435\u0444\u0430\u043a\u0442\u043e\u0440",
        "\u043f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
        "\u043a\u043e\u0434\u043e\u0432\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f",
        "review code",
    ]
    _SELF_IMPROVE_KW = [
        "\u0443\u043b\u0443\u0447\u0448\u0438 \u043e\u0442\u0432\u0435\u0442",
        "\u0441\u0430\u043c\u043e\u0430\u043d\u0430\u043b\u0438\u0437",
        "self-improve",
        "\u043f\u0435\u0440\u0435\u043f\u0440\u043e\u0432\u0435\u0440\u044c",
        "\u0434\u043e\u0440\u0430\u0431\u043e\u0442\u0430\u0439 \u043e\u0442\u0432\u0435\u0442",
    ]

    if any(x in text for x in _DIRECT_KW):
        scores["direct"] += 0.80
    if any(x in text for x in _PLANNER_KW):
        scores["planner"] += 0.95
    if any(x in text for x in _RESEARCH_KW):
        scores["task_graph"] += 0.80
    if any(x in text for x in _MULTI_AGENT_KW):
        scores["multi_agent"] += 0.95
    if any(x in text for x in _SELF_IMPROVE_KW):
        scores["self_improve"] += 1.10

    learned: list[dict] = []
    try:
        learned = get_v8_strategy_preferences(task, profile_name=memory_profile, limit=5)
    except Exception:
        learned = []

    for pref in learned:
        strategy = pref.get("strategy")
        if strategy not in scores:
            continue
        runs = max(int(pref.get("runs", 0) or 0), 1)
        success_rate = float(pref.get("success_rate", 0.0) or 0.0)
        avg_latency = float(pref.get("avg_latency", 0.0) or 0.0)
        learned_bonus = min(float(pref.get("score", 0.0)) / runs, 2.5) * 0.18
        latency_penalty = min(avg_latency / 10.0, 0.35)
        scores[strategy] += (success_rate * 0.95) + learned_bonus - latency_penalty

    strategy = max(scores.items(), key=lambda kv: kv[1])[0]
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_score = ordered[0][1]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    confidence = round(
        max(0.15, min(0.99, 0.45 + (top_score - second_score) / max(abs(top_score), 1.0))),
        2,
    )

    reason_parts = [f"mode={mode}"]
    if learned:
        best = learned[0]
        reason_parts.append(
            f"learned={best.get('strategy')} sr={best.get('success_rate')} runs={best.get('runs')}"
        )
    else:
        reason_parts.append("learned=no_history")

    return {
        "strategy": strategy,
        "confidence": confidence,
        "source": "learning_router",
        "reason": "; ".join(reason_parts),
        "scores": {k: round(v, 3) for k, v in sorted(scores.items())},
        "learned_preferences": learned,
    }
