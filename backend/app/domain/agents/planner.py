"""Planner and task-graph orchestration.

Extracted from core/agents.py — plan generation, normalization,
step execution, task-graph building, and graph execution with
auto-retry and reflection.

Heavy runtime helpers (run_browser_agent, run_terminal, persist_web_knowledge,
reflect_and_improve_answer) are imported lazily from app.core.agents to avoid
circular imports.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List
from urllib.parse import quote_plus

from app.core.llm import ask_model, clean_code_fence, safe_json_parse
from app.core.files import truncate_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None] | None


def extract_first_url(text: str) -> str:
    if not text:
        return ""
    m = re.search(r'https?://[^\s<>"\'\]]+', text)
    return m.group(0).rstrip(".,);]") if m else ""


def planner_safe_terminal_command(cmd: str) -> bool:
    from app.core.agents import is_dangerous_command

    low = (cmd or "").strip().lower()
    if not low or is_dangerous_command(low):
        return False
    allowed_prefixes = (
        "dir", "ls", "pwd", "where python", "python --version", "python -v",
        "pip list", "git status", "git branch", "git log --oneline",
        "type ", "cat ",
    )
    return low.startswith(allowed_prefixes)


# ---------------------------------------------------------------------------
# Default plan / graph generators
# ---------------------------------------------------------------------------

_SEARCH_KEYWORDS = [
    "\u043d\u0430\u0439\u0434\u0438", "\u043f\u043e\u0438\u0441\u043a",
    "\u0432\u0435\u0431", "\u0441\u0430\u0439\u0442",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446",
    "\u0438\u043d\u0442\u0435\u0440\u043d\u0435\u0442",
    "\u0441\u0442\u0440\u0430\u043d\u0438\u0446",
]

_MEMORY_KEYWORDS = [
    "\u0432\u0441\u043f\u043e\u043c\u043d\u0438",
    "\u043f\u0430\u043c\u044f\u0442\u044c",
    "\u0447\u0442\u043e \u0442\u044b \u0437\u043d\u0430\u0435\u0448\u044c",
    "\u0438\u0437 \u043f\u0430\u043c\u044f\u0442\u0438",
    "memory",
]


def planner_default_steps(task: str) -> List[dict]:
    url = extract_first_url(task)
    steps: List[dict] = []
    if url:
        steps.append({
            "tool": "browser",
            "goal": "\u041f\u0440\u043e\u0447\u0438\u0442\u0430\u0439 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443 \u0438 \u0438\u0437\u0432\u043b\u0435\u043a\u0438 \u0444\u0430\u043a\u0442\u044b, \u043f\u043e\u043b\u0435\u0437\u043d\u044b\u0435 \u0434\u043b\u044f \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438.",
            "url": url,
        })
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        steps.append({
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
        })
    steps.append({
        "tool": "reasoning",
        "goal": "\u0421\u043e\u0431\u0435\u0440\u0438 \u0432\u044b\u0432\u043e\u0434 \u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0448\u0430\u0433\u0438 \u043d\u0430 \u043e\u0441\u043d\u043e\u0432\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e\u0433\u043e \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430.",
    })
    return steps[:4]


def task_graph_default(task: str) -> List[dict]:
    url = extract_first_url(task)
    nodes: List[dict] = []

    if any(word in task.lower() for word in _MEMORY_KEYWORDS):
        nodes.append({
            "id": "n1",
            "tool": "memory_lookup",
            "goal": task[:400],
            "depends_on": [],
        })

    if url:
        nodes.append({
            "id": f"n{len(nodes) + 1}",
            "tool": "browser",
            "goal": "\u041f\u0440\u043e\u0447\u0438\u0442\u0430\u0439 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443 \u0438 \u0438\u0437\u0432\u043b\u0435\u043a\u0438 \u0444\u0430\u043a\u0442\u044b, \u043f\u043e\u043b\u0435\u0437\u043d\u044b\u0435 \u0434\u043b\u044f \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438.",
            "url": url,
            "depends_on": [],
        })
    elif any(word in task.lower() for word in _SEARCH_KEYWORDS):
        nodes.append({
            "id": f"n{len(nodes) + 1}",
            "tool": "browser",
            "goal": task[:400],
            "url": f"https://duckduckgo.com/?q={quote_plus(task[:200])}",
            "depends_on": [],
        })

    nodes.append({
        "id": f"n{len(nodes) + 1}",
        "tool": "reasoning",
        "goal": "\u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0435.",
        "depends_on": [n["id"] for n in nodes],
    })
    return nodes[:6]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

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
        deps = [str(d).strip() for d in deps if str(d).strip() and str(d).strip() != node_id]

        cleaned.append({
            "id": node_id,
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
            "depends_on": deps,
        })

    if not cleaned:
        return task_graph_default(task)

    if cleaned[-1]["tool"] != "reasoning":
        cleaned.append({
            "id": f"n{len(cleaned) + 1}",
            "tool": "reasoning",
            "goal": "\u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0435.",
            "url": "",
            "command": "",
            "depends_on": [n["id"] for n in cleaned],
        })

    valid_ids = {n["id"] for n in cleaned}
    for node in cleaned:
        node["depends_on"] = [d for d in node["depends_on"] if d in valid_ids and d != node["id"]]

    return cleaned[:7]


# ---------------------------------------------------------------------------
# Plan normalization for run_planner_agent
# ---------------------------------------------------------------------------

def normalize_planner_steps(raw_plan: Any, task: str) -> List[dict]:
    """Parse LLM output into a validated list of planner steps."""
    if not isinstance(raw_plan, list):
        return planner_default_steps(task)

    normalized: List[dict] = []
    for item in raw_plan[:4]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip().lower()
        if tool not in {"browser", "terminal", "reasoning"}:
            continue
        normalized.append({
            "tool": tool,
            "goal": str(item.get("goal", "")).strip() or task[:400],
            "url": str(item.get("url", "")).strip(),
            "command": str(item.get("command", "")).strip(),
        })
    if not normalized:
        return planner_default_steps(task)
    if normalized[-1]["tool"] != "reasoning":
        normalized.append({
            "tool": "reasoning",
            "goal": "\u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e \u0432\u0441\u0435\u043c \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u044f\u043c.",
            "url": "",
            "command": "",
        })
    return normalized[:5]


# ---------------------------------------------------------------------------
# make_task_graph — LLM-driven graph builder
# ---------------------------------------------------------------------------

def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    from app.core.memory import build_memory_context

    memory_context = build_memory_context(task, memory_profile, top_k=8)
    planner_prompt = (
        "\u0422\u044b \u0441\u0442\u0440\u043e\u0438\u0448\u044c task graph \u0434\u043b\u044f \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e\u0439 AI-\u0441\u0438\u0441\u0442\u0435\u043c\u044b. "
        "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON-\u043c\u0430\u0441\u0441\u0438\u0432 \u0443\u0437\u043b\u043e\u0432 \u0431\u0435\u0437 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0439. "
        "\u041a\u0430\u0436\u0434\u044b\u0439 \u0443\u0437\u0435\u043b \u0434\u043e\u043b\u0436\u0435\u043d \u0438\u043c\u0435\u0442\u044c \u0444\u043e\u0440\u043c\u0430\u0442:\n"
        '[{"id":"n1","tool":"browser|terminal|reasoning|memory_lookup",'
        '"goal":"...","url":"optional","command":"optional","depends_on":["n0"]}]\n\n'
        "\u041f\u0440\u0430\u0432\u0438\u043b\u0430:\n"
        "- \u0423\u0437\u043b\u044b \u0434\u043e\u043b\u0436\u043d\u044b \u0431\u044b\u0442\u044c \u043a\u043e\u0440\u043e\u0442\u043a\u0438\u043c\u0438 \u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u043d\u044b\u043c\u0438.\n"
        "- browser: \u0442\u043e\u043b\u044c\u043a\u043e \u0435\u0441\u043b\u0438 \u043d\u0443\u0436\u0435\u043d \u0432\u0435\u0431/\u0441\u0430\u0439\u0442/\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u044f/\u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0430.\n"
        "- terminal: \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u044b\u0445 read-only \u043a\u043e\u043c\u0430\u043d\u0434 \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0430\u043d\u0430\u043b\u0438\u0437\u0430.\n"
        "- memory_lookup: \u043a\u043e\u0433\u0434\u0430 \u043d\u0443\u0436\u043d\u043e \u043f\u043e\u0434\u043d\u044f\u0442\u044c \u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u0443\u044e \u043f\u0430\u043c\u044f\u0442\u044c \u043f\u0440\u043e\u0444\u0438\u043b\u044f.\n"
        "- reasoning: \u0434\u043b\u044f \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0438 \u0438 \u0441\u0438\u043d\u0442\u0435\u0437\u0430.\n"
        "- \u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0443\u0437\u0435\u043b \u0432\u0441\u0435\u0433\u0434\u0430 reasoning.\n"
        "- \u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c 6 \u0443\u0437\u043b\u043e\u0432.\n"
        "- \u041d\u0435 \u043f\u0440\u0438\u0434\u0443\u043c\u044b\u0432\u0430\u0439 \u043e\u043f\u0430\u0441\u043d\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b.\n\n"
        f"\u0417\u0430\u0434\u0430\u0447\u0430:\n{task}"
    )
    raw_graph = ask_model(
        model_name=model_name,
        profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
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


# ---------------------------------------------------------------------------
# run_planner_agent — plan → execute steps → reflect
# ---------------------------------------------------------------------------

def run_planner_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: ProgressCallback = None,
) -> Dict[str, Any]:
    from app.core.agents import (
        persist_web_knowledge,
        reflect_and_improve_answer,
        run_browser_agent,
        run_terminal,
    )
    from app.core.memory import build_memory_context, record_tool_usage

    total_steps = 3

    def _progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    memory_context = build_memory_context(task, memory_profile, top_k=8)

    # --- Step 1: build plan via LLM ---
    _progress(1, "\U0001f9ed Planner: \u0441\u0442\u0440\u043e\u044e \u043f\u043b\u0430\u043d...")
    planner_prompt = (
        "\u0422\u044b Planner agent. \u0420\u0430\u0437\u0431\u0435\u0439 \u0437\u0430\u0434\u0430\u0447\u0443 \u043d\u0430 2-4 \u0448\u0430\u0433\u0430 \u0438 \u0432\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON-\u043c\u0430\u0441\u0441\u0438\u0432 \u0431\u0435\u0437 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0439.\n"
        "\u0424\u043e\u0440\u043c\u0430\u0442 \u0448\u0430\u0433\u0430:\n"
        '[{"tool":"browser|terminal|reasoning","goal":"...","url":"optional","command":"optional"}]\n'
        "\u041f\u0440\u0430\u0432\u0438\u043b\u0430:\n"
        "- browser: \u0442\u043e\u043b\u044c\u043a\u043e \u0435\u0441\u043b\u0438 \u043d\u0443\u0436\u0435\u043d \u0432\u0435\u0431/\u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0430/\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u044f/\u043f\u043e\u0438\u0441\u043a.\n"
        "- terminal: \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0411\u0415\u0417\u041e\u041f\u0410\u0421\u041d\u042b\u0425 read-only \u043a\u043e\u043c\u0430\u043d\u0434 \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u043e\u0433\u043e \u0430\u043d\u0430\u043b\u0438\u0437\u0430.\n"
        "- reasoning: \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0448\u0430\u0433.\n"
        "- \u041d\u0435 \u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u0439 \u043e\u043f\u0430\u0441\u043d\u044b\u0435 \u043a\u043e\u043c\u0430\u043d\u0434\u044b.\n"
        "- \u0415\u0441\u043b\u0438 \u0432 \u0437\u0430\u0434\u0430\u0447\u0435 \u0435\u0441\u0442\u044c URL, \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 \u0435\u0433\u043e \u0432 browser step.\n"
        "- \u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0448\u0430\u0433 \u0432\u0441\u0435\u0433\u0434\u0430 reasoning.\n\n"
        f"\u0417\u0430\u0434\u0430\u0447\u0430:\n{task}"
    )
    raw_plan = ask_model(
        model_name=model_name,
        profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
        user_input=planner_prompt,
        memory_context=memory_context,
        use_memory=True,
        temp=0.05,
        include_history=False,
        num_ctx=num_ctx,
    )
    raw_plan = clean_code_fence(re.sub(r"^```json\s*", "", (raw_plan or "").strip()))
    plan = safe_json_parse(raw_plan)
    normalized_plan = normalize_planner_steps(plan, task)

    # --- Step 2: execute steps ---
    _progress(2, "\U0001f6e0 Planner: \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u044e \u0448\u0430\u0433\u0438...")
    steps_log: List[dict] = []
    gathered_contexts: List[str] = []

    for idx, step in enumerate(normalized_plan, start=1):
        tool = step["tool"]
        if tool == "browser":
            url = step.get("url") or extract_first_url(task)
            if not url:
                url = f"https://duckduckgo.com/?q={quote_plus(step['goal'][:200])}"
            result = run_browser_agent(url, step["goal"], max_pages=3)
            steps_log.append({
                "step": idx, "tool": tool, "goal": step["goal"], "url": url,
                "ok": result.get("ok", False),
                "trace": result.get("trace", []),
                "output": result.get("text", "")[:12000],
            })
            if result.get("ok"):
                gathered_contexts.append(
                    f"[BROWSER]\nURL: {url}\nGOAL: {step['goal']}\n"
                    f"{result.get('text', '')[:12000]}"
                )
                try:
                    persist_web_knowledge(
                        query=step["goal"],
                        web_context=result.get("text", ""),
                        profile_name=memory_profile,
                        source_kind="planner_browser",
                        url=url,
                        title=step["goal"],
                    )
                except Exception:
                    pass
        elif tool == "terminal":
            cmd = step.get("command", "")
            if not planner_safe_terminal_command(cmd):
                steps_log.append({
                    "step": idx, "tool": tool, "goal": step["goal"],
                    "command": cmd, "ok": False,
                    "output": "\u0428\u0430\u0433 \u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d: \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u043d\u0435 \u043f\u0440\u043e\u0448\u043b\u0430 safe-check planner-\u0430.",
                })
                continue
            output = run_terminal(cmd, timeout=20)
            steps_log.append({
                "step": idx, "tool": tool, "goal": step["goal"],
                "command": cmd, "ok": True,
                "output": output[:12000],
            })
            gathered_contexts.append(
                f"[TERMINAL]\nGOAL: {step['goal']}\nCOMMAND: {cmd}\n{output[:8000]}"
            )
        else:  # reasoning
            steps_log.append({
                "step": idx, "tool": tool, "goal": step["goal"],
                "ok": True,
                "output": "Reasoning step reserved for final synthesis.",
            })

    # --- Step 3: final synthesis ---
    _progress(3, "\U0001f3af Planner: \u0441\u043e\u0431\u0438\u0440\u0430\u044e \u0438\u0442\u043e\u0433...")
    context_blob = "\n\n".join(gathered_contexts)[:24000]
    final_prompt = (
        "\u0422\u044b Planner-Orchestrator. \u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044e \u043d\u0430 \u043e\u0441\u043d\u043e\u0432\u0435 \u0438\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0434\u0430\u0447\u0438 "
        "\u0438 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u043e\u0432 \u0448\u0430\u0433\u043e\u0432. \u0415\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e, \u0447\u0435\u0441\u0442\u043d\u043e \u0443\u043a\u0430\u0436\u0438 \u044d\u0442\u043e.\n\n"
        f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
        f"\u041f\u041b\u0410\u041d:\n{json.dumps(normalized_plan, ensure_ascii=False, indent=2)}\n\n"
        f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422 \u0428\u0410\u0413\u041e\u0412:\n{context_blob}\n\n"
        "\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430:\n"
        "1. \u041a\u0440\u0430\u0442\u043a\u0438\u0439 \u0438\u0442\u043e\u0433\n"
        "2. \u0427\u0442\u043e \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0437\u043d\u0430\u0442\u044c / \u0441\u0434\u0435\u043b\u0430\u0442\u044c\n"
        "3. \u041f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0435 \u0448\u0430\u0433\u0438"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(
        task, final_draft, model_name,
        extra_context=context_blob, num_ctx=num_ctx,
    )
    record_tool_usage(
        "planner_agent", task, True, score=1.2,
        notes="planner + execution + reflection",
        profile_name=memory_profile,
    )

    return {
        "plan": normalized_plan,
        "steps": steps_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }


# ---------------------------------------------------------------------------
# Dependency context helper for task graph execution
# ---------------------------------------------------------------------------

def task_graph_context_from_deps(
    node: dict[str, Any],
    node_results: Dict[str, dict],
) -> str:
    parts = []
    for dep in node.get("depends_on", []):
        res = node_results.get(dep)
        if not res:
            continue
        snippet = truncate_text(str(res.get("output", "")), 6000)
        parts.append(f"[{dep} \u00b7 {res.get('tool', '')}]\n{snippet}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# run_task_graph — build graph → execute nodes → auto-retry → reflect
# ---------------------------------------------------------------------------

def run_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback: ProgressCallback = None,
) -> Dict[str, Any]:
    from app.core.agents import (
        persist_web_knowledge,
        reflect_and_improve_answer,
        run_browser_agent,
        run_terminal,
    )
    from app.core.memory import build_memory_context, record_tool_usage

    memory_context = build_memory_context(task, memory_profile, top_k=8)
    graph = make_task_graph(task, model_name, memory_profile, num_ctx=num_ctx)

    total_steps = max(len(graph) + 1, 2)

    def _progress(step: int, label: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, label)

    node_results: Dict[str, dict] = {}
    execution_log: List[dict] = []
    remaining = list(graph)
    step_idx = 0

    while remaining:
        progressed = False
        for node in remaining[:]:
            deps = node.get("depends_on", [])
            if any(dep not in node_results for dep in deps):
                continue

            step_idx += 1
            _progress(step_idx, f"\U0001f578 \u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e {node['id']} \u00b7 {node['tool']}")
            dep_context = task_graph_context_from_deps(node, node_results)
            tool = node["tool"]

            if tool == "browser":
                url = node.get("url") or extract_first_url(task)
                if not url:
                    url = f"https://duckduckgo.com/?q={quote_plus(node['goal'][:200])}"
                result = run_browser_agent(url, node["goal"], max_pages=3)
                out = result.get("text", "")
                node_results[node["id"]] = {
                    "id": node["id"], "tool": tool, "goal": node["goal"],
                    "ok": result.get("ok", False), "url": url,
                    "trace": result.get("trace", []),
                    "output": out[:15000],
                }
                if result.get("ok"):
                    try:
                        persist_web_knowledge(
                            query=node["goal"],
                            web_context=out,
                            profile_name=memory_profile,
                            source_kind="task_graph_browser",
                            url=url,
                            title=node["goal"],
                        )
                    except Exception:
                        pass

            elif tool == "terminal":
                cmd = node.get("command", "").strip()
                if not planner_safe_terminal_command(cmd):
                    node_results[node["id"]] = {
                        "id": node["id"], "tool": tool, "goal": node["goal"],
                        "ok": False, "command": cmd,
                        "output": "\u0423\u0437\u0435\u043b \u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d: \u043a\u043e\u043c\u0430\u043d\u0434\u0430 \u043d\u0435 \u043f\u0440\u043e\u0448\u043b\u0430 safe-check Task Graph.",
                    }
                else:
                    out = run_terminal(cmd, timeout=20)
                    node_results[node["id"]] = {
                        "id": node["id"], "tool": tool, "goal": node["goal"],
                        "ok": True, "command": cmd,
                        "output": out[:12000],
                    }

            elif tool == "memory_lookup":
                lookup_q = node.get("goal") or task
                mem = build_memory_context(lookup_q, memory_profile, top_k=8)
                node_results[node["id"]] = {
                    "id": node["id"], "tool": tool, "goal": node["goal"],
                    "ok": True,
                    "output": mem[:12000] if mem else "\u0420\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u0430\u044f \u043f\u0430\u043c\u044f\u0442\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430.",
                }

            else:  # reasoning
                reasoning_prompt = (
                    "\u0422\u044b reasoning-node \u0432 task graph. \u0412\u044b\u043f\u043e\u043b\u043d\u0438 \u0442\u043e\u043b\u044c\u043a\u043e \u0437\u0430\u0434\u0430\u0447\u0443 \u044d\u0442\u043e\u0433\u043e \u0443\u0437\u043b\u0430, "
                    "\u043e\u043f\u0438\u0440\u0430\u044f\u0441\u044c \u043d\u0430 \u0438\u0441\u0445\u043e\u0434\u043d\u0443\u044e \u0437\u0430\u0434\u0430\u0447\u0443 \u0438 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0437\u0430\u0432\u0438\u0441\u0438\u043c\u043e\u0441\u0442\u0435\u0439. "
                    "\u0415\u0441\u043b\u0438 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430 \u043c\u0430\u043b\u043e \u2014 \u0441\u043a\u0430\u0436\u0438 \u043e\u0431 \u044d\u0442\u043e\u043c \u043f\u0440\u044f\u043c\u043e.\n\n"
                    f"\u0418\u0421\u0425\u041e\u0414\u041d\u0410\u042f \u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
                    f"\u0417\u0410\u0414\u0410\u0427\u0410 \u0423\u0417\u041b\u0410:\n{node['goal']}\n\n"
                    "\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422 \u0417\u0410\u0412\u0418\u0421\u0418\u041c\u041e\u0421\u0422\u0415\u0419:\n"
                    + (dep_context or "\u041d\u0435\u0442 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430 \u0437\u0430\u0432\u0438\u0441\u0438\u043c\u043e\u0441\u0442\u0435\u0439.")
                )
                out = ask_model(
                    model_name=model_name,
                    profile_name="\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
                    user_input=reasoning_prompt,
                    memory_context=memory_context,
                    use_memory=True,
                    include_history=False,
                    num_ctx=num_ctx,
                )
                node_results[node["id"]] = {
                    "id": node["id"], "tool": tool, "goal": node["goal"],
                    "ok": True,
                    "output": out[:15000],
                }

            execution_log.append(node_results[node["id"]])
            remaining.remove(node)
            progressed = True

        if not progressed:
            for node in remaining:
                node_results[node["id"]] = {
                    "id": node["id"], "tool": node["tool"], "goal": node["goal"],
                    "ok": False,
                    "output": "\u0423\u0437\u0435\u043b \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d: \u0437\u0430\u0432\u0438\u0441\u0438\u043c\u043e\u0441\u0442\u0438 \u043d\u0435 \u0440\u0430\u0437\u0440\u0435\u0448\u0438\u043b\u0438\u0441\u044c \u0438\u043b\u0438 \u0433\u0440\u0430\u0444 \u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u0435\u043d.",
                }
                execution_log.append(node_results[node["id"]])
            break

    # --- Auto-retry failed browser/terminal nodes ---
    retried: List[dict] = []
    for item in list(execution_log):
        if item.get("ok"):
            continue
        if item.get("tool") == "browser":
            retry_url = (
                item.get("url")
                or f"https://duckduckgo.com/?q={quote_plus(item.get('goal', task)[:200])}"
            )
            retry = run_browser_agent(retry_url, item.get("goal", task), max_pages=2)
            retry_node = {
                "id": f"{item['id']}_retry",
                "tool": "browser_retry",
                "goal": item.get("goal", task),
                "ok": retry.get("ok", False),
                "url": retry_url,
                "trace": retry.get("trace", []),
                "output": retry.get("text", "")[:12000],
            }
            retried.append(retry_node)
            if retry.get("ok"):
                try:
                    persist_web_knowledge(
                        query=item.get("goal", task),
                        web_context=retry.get("text", ""),
                        profile_name=memory_profile,
                        source_kind="task_graph_browser_retry",
                        url=retry_url,
                        title=item.get("goal", task),
                    )
                except Exception:
                    pass
        elif item.get("tool") == "terminal":
            retried.append({
                "id": f"{item['id']}_retry",
                "tool": "reasoning_retry",
                "goal": item.get("goal", task),
                "ok": True,
                "output": "Terminal \u0448\u0430\u0433 \u043d\u0435 \u043f\u0440\u043e\u0448\u0451\u043b safe-check. "
                         "\u0414\u043b\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f \u043f\u0440\u043e\u0433\u0440\u0435\u0441\u0441\u0430 "
                         "\u0433\u0440\u0430\u0444 \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0451\u043d \u043d\u0430 reasoning fallback.",
            })
    execution_log.extend(retried)

    # --- Final synthesis ---
    _progress(total_steps, "\U0001f3af Task Graph: \u0441\u043e\u0431\u0438\u0440\u0430\u044e \u0438\u0442\u043e\u0433...")
    state_blob = "\n\n".join(
        f"[{item['id']} \u00b7 {item['tool']} \u00b7 {'OK' if item.get('ok') else 'FAIL'}]\n"
        f"GOAL: {item.get('goal', '')}\n"
        f"{truncate_text(str(item.get('output', '')), 5000)}"
        for item in execution_log
    )[:30000]

    final_prompt = (
        "\u0422\u044b Task Graph Orchestrator. \u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044e \u043d\u0430 \u043e\u0441\u043d\u043e\u0432\u0435 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u0433\u0440\u0430\u0444\u0430. "
        "\u0427\u0435\u0441\u0442\u043d\u043e \u043e\u0442\u043c\u0435\u0447\u0430\u0439, \u0435\u0441\u043b\u0438 \u043a\u0430\u043a\u043e\u0439-\u0442\u043e \u0443\u0437\u0435\u043b \u043d\u0435 \u0441\u0440\u0430\u0431\u043e\u0442\u0430\u043b \u0438\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u0435 \u0445\u0432\u0430\u0442\u0438\u043b\u043e.\n\n"
        f"\u0418\u0421\u0425\u041e\u0414\u041d\u0410\u042f \u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
        f"\u0413\u0420\u0410\u0424:\n{json.dumps(graph, ensure_ascii=False, indent=2)}\n\n"
        f"STATE:\n{state_blob}\n\n"
        "\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430 \u043e\u0442\u0432\u0435\u0442\u0430:\n"
        "1. \u041a\u0440\u0430\u0442\u043a\u0438\u0439 \u0438\u0442\u043e\u0433\n"
        "2. \u0427\u0442\u043e \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0437\u043d\u0430\u0442\u044c / \u0441\u0434\u0435\u043b\u0430\u0442\u044c\n"
        "3. \u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u044f \u0438\u043b\u0438 \u0441\u0431\u043e\u0438\n"
        "4. \u041f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0435 \u0448\u0430\u0433\u0438"
    )
    final_draft = ask_model(
        model_name=model_name,
        profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
        user_input=final_prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    reflected = reflect_and_improve_answer(
        task, final_draft, model_name,
        extra_context=state_blob, num_ctx=num_ctx,
    )
    graph_ok = any(item.get("ok") for item in execution_log)
    record_tool_usage(
        "task_graph", task, graph_ok,
        score=1.3 if graph_ok else 0.6,
        notes="task graph + auto-retry + reflection",
        profile_name=memory_profile,
    )

    return {
        "graph": graph,
        "steps": execution_log,
        "final": reflected["final"],
        "reflection": reflected["critique"],
    }
