"""Planner runtime helpers extracted from planner.py.

Keeps browser/terminal execution paths isolated from the LLM
plan/graph construction logic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus

from app.core.files import truncate_text
from app.core.llm import ask_model
from app.domain.agents.planner_prompts import build_task_graph_reasoning_prompt
from app.domain.tools.browser_agent_tool import run_browser_agent
from app.domain.tools.terminal_tool import is_dangerous_command, run_terminal


def extract_first_url(text: str) -> str:
    if not text:
        return ""
    match = re.search(r'https?://[^\s<>"\'\]]+', text)
    return match.group(0).rstrip(".,);]") if match else ""


def planner_safe_terminal_command(cmd: str) -> bool:
    low = (cmd or "").strip().lower()
    if not low or is_dangerous_command(low):
        return False
    allowed_prefixes = (
        "dir", "ls", "pwd", "where python", "python --version", "python -v",
        "pip list", "git status", "git branch", "git log --oneline",
        "type ", "cat ",
    )
    return low.startswith(allowed_prefixes)


def execute_planner_step(
    *,
    idx: int,
    step: dict,
    task: str,
) -> Tuple[dict, str | None]:
    tool = step["tool"]
    if tool == "browser":
        url = step.get("url") or extract_first_url(task)
        if not url:
            url = f"https://duckduckgo.com/?q={quote_plus(step['goal'][:200])}"
        result = run_browser_agent(url, step["goal"], max_pages=3)
        output = result.get("text", "")
        step_log = {
            "step": idx,
            "tool": tool,
            "goal": step["goal"],
            "url": url,
            "ok": result.get("ok", False),
            "trace": result.get("trace", []),
            "output": output[:12000],
        }
        if result.get("ok"):
            gathered_context = (
                f"[BROWSER]\nURL: {url}\nGOAL: {step['goal']}\n{output[:12000]}"
            )
            return step_log, gathered_context
        return step_log, None

    if tool == "terminal":
        cmd = step.get("command", "")
        if not planner_safe_terminal_command(cmd):
            return {
                "step": idx,
                "tool": tool,
                "goal": step["goal"],
                "command": cmd,
                "ok": False,
                "output": "Шаг пропущен: команда не прошла safe-check planner-а.",
            }, None
        output = run_terminal(cmd, timeout=20)
        step_log = {
            "step": idx,
            "tool": tool,
            "goal": step["goal"],
            "command": cmd,
            "ok": True,
            "output": output[:12000],
        }
        gathered_context = (
            f"[TERMINAL]\nGOAL: {step['goal']}\nCOMMAND: {cmd}\n{output[:8000]}"
        )
        return step_log, gathered_context

    return {
        "step": idx,
        "tool": tool,
        "goal": step["goal"],
        "ok": True,
        "output": "Reasoning step reserved for final synthesis.",
    }, None


def task_graph_context_from_deps(
    node: dict[str, Any],
    node_results: Dict[str, dict],
) -> str:
    parts: List[str] = []
    for dep in node.get("depends_on", []):
        result = node_results.get(dep)
        if not result:
            continue
        snippet = truncate_text(str(result.get("output", "")), 6000)
        parts.append(f"[{dep} · {result.get('tool', '')}]\n{snippet}")
    return "\n\n".join(parts)


def execute_task_graph_node(
    *,
    task: str,
    node: dict[str, Any],
    model_name: str,
    node_results: Dict[str, dict],
    num_ctx: int = 4096,
) -> dict:
    dep_context = task_graph_context_from_deps(node, node_results)
    tool = node["tool"]

    if tool == "browser":
        url = node.get("url") or extract_first_url(task)
        if not url:
            url = f"https://duckduckgo.com/?q={quote_plus(node['goal'][:200])}"
        result = run_browser_agent(url, node["goal"], max_pages=3)
        output = result.get("text", "")
        return {
            "id": node["id"],
            "tool": tool,
            "goal": node["goal"],
            "ok": result.get("ok", False),
            "url": url,
            "trace": result.get("trace", []),
            "output": output[:15000],
        }

    if tool == "terminal":
        cmd = node.get("command", "").strip()
        if not planner_safe_terminal_command(cmd):
            return {
                "id": node["id"],
                "tool": tool,
                "goal": node["goal"],
                "ok": False,
                "command": cmd,
                "output": "Узел пропущен: команда не прошла safe-check Task Graph.",
            }
        output = run_terminal(cmd, timeout=20)
        return {
            "id": node["id"],
            "tool": tool,
            "goal": node["goal"],
            "ok": True,
            "command": cmd,
            "output": output[:12000],
        }

    reasoning_prompt = build_task_graph_reasoning_prompt(
        task=task,
        node_goal=node["goal"],
        dep_context=dep_context,
    )
    output = ask_model(
        model_name=model_name,
        profile_name="Аналитик",
        user_input=reasoning_prompt,
        memory_context="",
        use_memory=True,
        include_history=False,
        num_ctx=num_ctx,
    )
    return {
        "id": node["id"],
        "tool": tool,
        "goal": node["goal"],
        "ok": True,
        "output": output[:15000],
    }


def retry_failed_task_graph_steps(
    *,
    task: str,
    execution_log: List[dict],
) -> List[dict]:
    retried: List[dict] = []
    for item in execution_log:
        if item.get("ok"):
            continue
        if item.get("tool") == "browser":
            retry_url = (
                item.get("url")
                or f"https://duckduckgo.com/?q={quote_plus(item.get('goal', task)[:200])}"
            )
            retry = run_browser_agent(retry_url, item.get("goal", task), max_pages=2)
            retried.append({
                "id": f"{item['id']}_retry",
                "tool": "browser_retry",
                "goal": item.get("goal", task),
                "ok": retry.get("ok", False),
                "url": retry_url,
                "trace": retry.get("trace", []),
                "output": retry.get("text", "")[:12000],
            })
        elif item.get("tool") == "terminal":
            retried.append({
                "id": f"{item['id']}_retry",
                "tool": "reasoning_retry",
                "goal": item.get("goal", task),
                "ok": True,
                "output": (
                    "Terminal шаг не прошёл safe-check. "
                    "Для сохранения прогресса граф переключён на reasoning fallback."
                ),
            })
    return retried


def build_task_graph_state_blob(execution_log: List[dict]) -> str:
    return "\n\n".join(
        f"[{item['id']} · {item['tool']} · {'OK' if item.get('ok') else 'FAIL'}]\n"
        f"GOAL: {item.get('goal', '')}\n"
        f"{truncate_text(str(item.get('output', '')), 5000)}"
        for item in execution_log
    )[:30000]
