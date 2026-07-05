"""Code-agent tool: reconcile server facts.

Read-only. Probes the live llama-server (/props) and reports the current active
model + context window plus any drift from the last recorded values. Not in the
base tool set — tool_search surfaces it only when the conversation is about the
server / active model / context window, so it never spams unrelated chats.
"""
from __future__ import annotations

from typing import Any


def tool_reconcile_server_facts(**_kw: Any) -> dict[str, str]:
    from app.application.drift.probes import FACT_LABELS
    from app.application.drift.runtime import reconcile

    res = reconcile()
    if not res.get("reachable"):
        return {
            "text": "Живой сервер (/props) недоступен — свежие факты получить не удалось; "
            "известные значения не менялись."
        }

    lines = ["Сверено с живым сервером (/props):"]
    for fact in res.get("facts", []):
        if fact.get("verified"):
            label = FACT_LABELS.get(fact["key"], fact["key"])
            lines.append(f"- {label}: {fact['value']}")

    drifts = res.get("drifts") or []
    lines.append("")
    if drifts:
        lines.append("ДРЕЙФ (изменилось с прошлой проверки):")
        for d in drifts:
            label = FACT_LABELS.get(d["key"], d["key"])
            lines.append(f"- {label}: было {d['previous']!r} → стало {d['current']!r}")
    else:
        lines.append("Дрейфа нет — значения совпадают с записанными.")

    return {"text": "\n".join(lines)}
