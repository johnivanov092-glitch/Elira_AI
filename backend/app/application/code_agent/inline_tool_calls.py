"""Recovery of tool calls that local models emit as plain text/JSON in
``message.content`` instead of the structured ``message.tool_calls`` field.

Extracted verbatim from agent_loop.py (no behaviour change). Only
``_extract_inline_tool_calls`` is used by the agent loop; the other two are
its internal helpers.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator


def _iter_json_object_substrings(text: str) -> Iterator[str]:
    """Yield every balanced top-level JSON-object substring inside `text`.
    Used to recover from models that emit multiple ```json blocks back to
    back, or just multiple JSON objects with prose around them.
    """
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            ch = text[j]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    i = j + 1
                    break
        else:
            # Unbalanced; stop scanning
            break


def _extract_inline_tool_calls(content: str, known_tools: set[str]) -> list[dict[str, Any]]:
    """Some local models emit tool calls as plain JSON in `message.content`
    instead of populating the structured `message.tool_calls` field. This
    helper recovers those so the agent loop still progresses.

    Recognized formats (any may appear in code fences, multiple times,
    with prose around them):
      {"name": "tool", "arguments": {...}}
      {"name": "tool", "parameters": {...}}
      [{"name": ...}, ...]
      {"tool_calls": [{...}]}
      {"function": {"name": ..., "arguments": {...}}}

    Tool names not present in `known_tools` are dropped (the model
    hallucinated). Returns a normalized list shaped like
    `tool_calls`: [{"function": {"name": ..., "arguments": {...}}}].

    Multiple JSON objects in one content string are all returned — the
    agent loop will execute them in order.
    """
    if not content:
        return []

    def _normalize(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        # Unwrap {"function": {...}}
        if "function" in item and isinstance(item["function"], dict):
            fn = item["function"]
            name = fn.get("name")
            args = fn.get("arguments") or fn.get("parameters") or {}
        else:
            name = item.get("name")
            args = item.get("arguments") or item.get("parameters") or {}
        if not isinstance(name, str) or name not in known_tools:
            return None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return {"function": {"name": name, "arguments": args}}

    out: list[dict[str, Any]] = []
    # llama.cpp models occasionally emit the Qwen XML-ish tool syntax instead
    # of OpenAI tool_calls. Recover only known tools and JSON object arguments.
    xml_call = re.compile(
        r"<tool_call>\s*<function=([A-Za-z_][\w.-]*)>\s*(.*?)\s*</function>\s*</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )
    for match in xml_call.finditer(content):
        name = match.group(1)
        if name not in known_tools:
            continue
        try:
            args = json.loads(match.group(2).strip() or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"function": {"name": name, "arguments": args}})

    # Scan ALL JSON-object substrings (handles multiple ```json blocks,
    # arrays of tool calls embedded in prose, etc.)
    for raw in _iter_json_object_substrings(content):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
                for it in parsed["tool_calls"]:
                    norm = _normalize(it)
                    if norm:
                        out.append(norm)
            else:
                norm = _normalize(parsed)
                if norm:
                    out.append(norm)

    # Also try top-level array (rare but seen): "[{...},{...}]"
    if not out:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
            stripped = stripped.strip()
        if stripped.startswith("["):
            try:
                arr = json.loads(stripped)
                if isinstance(arr, list):
                    for it in arr:
                        norm = _normalize(it)
                        if norm:
                            out.append(norm)
            except json.JSONDecodeError:
                pass

    # Fallback: call-expression syntax `tool_name(key="value", ...)`. Some
    # models write the call as pseudo-code inside a ```bash/code
    # fence instead of JSON. Only for known tools, only if no JSON-format call
    # was recovered, and only for lines that ARE the call — nothing but the
    # call expression on the line (fences stripped). A tool name mentioned
    # inside prose (e.g. a final answer saying «я запустил run_bash(...) и всё
    # зелёное») must NOT be re-executed as a new call.
    if not out and known_tools:
        name_alt = "|".join(re.escape(t) for t in sorted(known_tools, key=len, reverse=True))
        pure_call = re.compile(rf"^\s*({name_alt})\s*\(([^()]*)\)\s*;?\s*$")
        for line in content.splitlines():
            if line.strip().startswith("```"):
                continue  # fence marker lines
            m = pure_call.match(line)
            if m:
                args = _parse_call_expr_args(m.group(2))
                if isinstance(args, dict):
                    out.append({"function": {"name": m.group(1), "arguments": args}})

    return out


def _contains_tool_trace(content: str) -> bool:
    lowered = str(content or "").lower()
    return "<tool_call" in lowered or "<function=" in lowered


_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>.*?</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION_BLOCK_RE = re.compile(r"<function=[^>]*>.*?</function>", re.DOTALL | re.IGNORECASE)
_STRAY_TOOL_TAG_RE = re.compile(r"</?(?:tool_call|function|parameter)\b[^>]*>", re.IGNORECASE)


def _strip_tool_call_markup(text: str) -> str:
    """Display safety net: remove leaked `<tool_call>…</tool_call>` / `<function=…>`
    markup from an ANSWER so a degenerate model's raw tool-call syntax never shows
    up as text in the chat (live: it leaked into a wrap-up after the loop-guard
    fired). Whole blocks go first; orphan tags left by a truncated block are then
    swept. Only touches strings that actually contain the markers."""
    if not _contains_tool_trace(text):
        return text or ""
    t = _TOOL_CALL_BLOCK_RE.sub("", text)
    t = _FUNCTION_BLOCK_RE.sub("", t)
    t = _STRAY_TOOL_TAG_RE.sub("", t)
    return t.strip()


def _parse_call_expr_args(arg_str: str) -> dict[str, Any]:
    """Parse `key="value", key2='v2', key3=123, key4=true` from a call
    expression. Best-effort: respects quotes, falls back to bare tokens."""
    args: dict[str, Any] = {}
    pair = re.compile(
        r"""(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,]+)"""
    )
    for m in pair.finditer(arg_str or ""):
        key = m.group(1)
        raw = m.group(2).strip()
        if (raw[:1], raw[-1:]) in (('"', '"'), ("'", "'")):
            inner = raw[1:-1]
            value: Any = inner.encode().decode("unicode_escape") if "\\" in inner else inner
        else:
            low = raw.lower()
            if low in ("true", "false"):
                value = low == "true"
            else:
                try:
                    value = int(raw)
                except ValueError:
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw
        args[key] = value
    return args
