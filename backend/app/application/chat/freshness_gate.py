from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FreshnessGateResult:
    ok: bool
    answer: str = ""
    reason: str = ""
    details: dict[str, Any] | None = None


def requires_freshness_check(temporal: dict[str, Any] | None) -> bool:
    temporal = temporal or {}
    return bool(temporal.get("requires_web") or temporal.get("freshness_sensitive"))


def _latest_web_result(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(tool_results or []):
        if item.get("tool") != "web_search":
            continue
        result = item.get("result")
        return result if isinstance(result, dict) else {}
    return None


def evaluate_freshness_gate(
    *,
    temporal: dict[str, Any] | None,
    selected_tools: list[str],
    tool_results: list[dict[str, Any]],
) -> FreshnessGateResult:
    """Fail closed for current/freshness-sensitive chat answers.

    The planner and web layer already try to search. This gate prevents the final
    model answer when the request requires fresh evidence but no web evidence was
    actually gathered. It is intentionally narrow: stable or non-web queries are
    unaffected, and weak-but-present evidence is allowed through with the
    existing freshness note in the prompt.
    """
    if not requires_freshness_check(temporal):
        return FreshnessGateResult(ok=True)

    if "web_search" not in set(selected_tools or []):
        return FreshnessGateResult(
            ok=False,
            reason="web_search_not_enabled",
            answer=(
                "Запрос требует свежей интернет-проверки, но веб-поиск для этого запуска "
                "не включён. Я не буду отвечать из старой памяти, чтобы не выдать "
                "устаревшие данные."
            ),
            details={"selected_tools": list(selected_tools or [])},
        )

    web_result = _latest_web_result(tool_results)
    if web_result is None:
        return FreshnessGateResult(
            ok=False,
            reason="web_search_not_run",
            answer=(
                "Запрос требует свежей интернет-проверки, но веб-поиск фактически "
                "не был выполнен. Я не буду отвечать без подтверждения источниками."
            ),
            details={"selected_tools": list(selected_tools or [])},
        )

    found = int(web_result.get("found") or web_result.get("count") or 0)
    news = int(web_result.get("news") or web_result.get("news_hits") or 0)
    fetched_pages = int(web_result.get("fetched_pages") or 0)
    has_current_evidence = bool(web_result.get("has_current_evidence"))
    has_any_evidence = found > 0 or news > 0 or fetched_pages > 0 or has_current_evidence

    if not has_any_evidence:
        return FreshnessGateResult(
            ok=False,
            reason="web_search_no_evidence",
            answer=(
                "Я проверил интернет, но не получил надёжных источников по этому "
                "свежему запросу. Поэтому не буду уверенно отвечать: данные сейчас "
                "не подтверждены."
            ),
            details={
                "found": found,
                "news": news,
                "fetched_pages": fetched_pages,
                "freshness_state": web_result.get("freshness_state"),
                "has_current_evidence": has_current_evidence,
            },
        )

    return FreshnessGateResult(
        ok=True,
        details={
            "found": found,
            "news": news,
            "fetched_pages": fetched_pages,
            "freshness_state": web_result.get("freshness_state"),
            "has_current_evidence": has_current_evidence,
        },
    )
