"""Planner graph builders and normalization helpers."""
from __future__ import annotations

import re
from typing import Any, List
from urllib.parse import quote_plus

from app.core.llm import ask_model, clean_code_fence, safe_json_parse
from app.domain.agents.planner_prompts import build_task_graph_prompt
from app.domain.agents.planner_runtime import extract_first_url


_SEARCH_KEYWORDS = [
    "РЅР°Р№РґРё",
    "РїРѕРёСЃРє",
    "РІРµР±",
    "СЃР°Р№С‚",
    "РґРѕРєСѓРјРµРЅС‚Р°С†",
    "РёРЅС‚РµСЂРЅРµС‚",
    "СЃС‚СЂР°РЅРёС†",
]

_MEMORY_KEYWORDS = [
    "РІСЃРїРѕРјРЅРё",
    "РїР°РјСЏС‚СЊ",
    "С‡С‚Рѕ С‚С‹ Р·РЅР°РµС€СЊ",
    "РёР· РїР°РјСЏС‚Рё",
    "memory",
]


def planner_default_steps(task: str) -> List[dict]:
    url = extract_first_url(task)
    steps: List[dict] = []
    if url:
        steps.append(
            {
                "tool": "browser",
                "goal": "РџСЂРѕС‡РёС‚Р°Р№ СЃС‚СЂР°РЅРёС†Сѓ Рё РёР·РІР»РµРєРё С„Р°РєС‚С‹, РїРѕР»РµР·РЅС‹Рµ РґР»СЏ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рё.",
                "url": url,
            }
        )
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        steps.append(
            {
                "tool": "browser",
                "goal": task[:400],
                "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
            }
        )
    steps.append(
        {
            "tool": "reasoning",
            "goal": "РЎРѕР±РµСЂРё РІС‹РІРѕРґ Рё РїСЂР°РєС‚РёС‡РµСЃРєРёРµ С€Р°РіРё РЅР° РѕСЃРЅРѕРІРµ РґРѕСЃС‚СѓРїРЅРѕРіРѕ РєРѕРЅС‚РµРєСЃС‚Р°.",
        }
    )
    return steps[:4]


def task_graph_default(task: str) -> List[dict]:
    url = extract_first_url(task)
    nodes: List[dict] = []

    if any(word in task.lower() for word in _MEMORY_KEYWORDS):
        nodes.append(
            {
                "id": "n1",
                "tool": "memory_lookup",
                "goal": task[:400],
                "depends_on": [],
            }
        )

    if url:
        nodes.append(
            {
                "id": f"n{len(nodes) + 1}",
                "tool": "browser",
                "goal": "РџСЂРѕС‡РёС‚Р°Р№ СЃС‚СЂР°РЅРёС†Сѓ Рё РёР·РІР»РµРєРё С„Р°РєС‚С‹, РїРѕР»РµР·РЅС‹Рµ РґР»СЏ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рё.",
                "url": url,
                "depends_on": [],
            }
        )
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        nodes.append(
            {
                "id": f"n{len(nodes) + 1}",
                "tool": "browser",
                "goal": task[:400],
                "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
                "depends_on": [],
            }
        )

    nodes.append(
        {
            "id": f"n{len(nodes) + 1}",
            "tool": "reasoning",
            "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёР№ РѕС‚РІРµС‚ РїРѕ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рµ.",
            "depends_on": [node["id"] for node in nodes],
        }
    )
    return nodes[:6]


def normalize_task_graph(raw_graph: Any, task: str) -> List[dict]:
    if not isinstance(raw_graph, list):
        return task_graph_default(task)

    cleaned: List[dict] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(raw_graph[:6], start=1):
        if not isinstance(item, dict):
            continue

        node_id = str(item.get("id", "")).strip() or f"n{idx}"
        if node_id in seen_ids:
            node_id = f"n{idx}"
        seen_ids.add(node_id)

        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning", "memory_lookup"}:
            continue

        deps = item.get("depends_on", [])
        if not isinstance(deps, list):
            deps = []
        deps = [str(dep).strip() for dep in deps if str(dep).strip() and str(dep).strip() != node_id]

        cleaned.append(
            {
                "id": node_id,
                "tool": tool,
                "goal": str(item.get("goal", "")).strip() or task[:400],
                "url": str(item.get("url", "")).strip(),
                "command": str(item.get("command", "")).strip(),
                "depends_on": deps,
            }
        )

    if not cleaned:
        return task_graph_default(task)

    if cleaned[-1]["tool"] != "reasoning":
        cleaned.append(
            {
                "id": f"n{len(cleaned) + 1}",
                "tool": "reasoning",
                "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ Р°РЅР°Р»РёС‚РёС‡РµСЃРєРёР№ РѕС‚РІРµС‚ РїРѕ РёСЃС…РѕРґРЅРѕР№ Р·Р°РґР°С‡Рµ.",
                "url": "",
                "command": "",
                "depends_on": [node["id"] for node in cleaned],
            }
        )

    valid_ids = {node["id"] for node in cleaned}
    for node in cleaned:
        node["depends_on"] = [dep for dep in node["depends_on"] if dep in valid_ids and dep != node["id"]]

    return cleaned[:7]


def normalize_planner_steps(raw_plan: Any, task: str) -> List[dict]:
    if not isinstance(raw_plan, list):
        return planner_default_steps(task)

    normalized: List[dict] = []
    for item in raw_plan[:4]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning"}:
            continue
        normalized.append(
            {
                "tool": tool,
                "goal": str(item.get("goal", "")).strip() or task[:400],
                "url": str(item.get("url", "")).strip(),
                "command": str(item.get("command", "")).strip(),
            }
        )
    if not normalized:
        return planner_default_steps(task)
    if normalized[-1]["tool"] != "reasoning":
        normalized.append(
            {
                "tool": "reasoning",
                "goal": "РЎРѕР±РµСЂРё С„РёРЅР°Р»СЊРЅС‹Р№ РѕС‚РІРµС‚ РїРѕ РІСЃРµРј РЅР°Р±Р»СЋРґРµРЅРёСЏРј.",
                "url": "",
                "command": "",
            }
        )
    return normalized[:5]


def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    from app.application.memory.context import build_default_memory_context

    memory_context = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )
    planner_prompt = build_task_graph_prompt(task)
    raw_graph = ask_model(
        model_name=model_name,
        profile_name="РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_graph = clean_code_fence(re.sub(r"^```json\s*", "", (raw_graph or "").strip()))
    parsed = safe_json_parse(raw_graph)
    return normalize_task_graph(parsed, task)
