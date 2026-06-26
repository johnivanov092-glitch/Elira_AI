"""Shared sanitizing helpers for MCP transports (stdio + HTTP).

MCP servers are untrusted remote code: their resource and prompt payloads
flow into the agent's context, so every text field is length-bounded and
wrapped in explicit UNTRUSTED markers before it can reach the model. These
helpers are transport-agnostic — both `McpClient` (stdio) and
`McpHttpClient` import them so the two paths stay byte-for-byte identical.
"""
from __future__ import annotations

from typing import Any


# Default cap on how many characters a single resource/prompt read may
# contribute to the agent's context. Transports pass this through to the
# sanitizers below; callers can override per-call.
DEFAULT_CONTEXT_RESULT_LIMIT = 50_000


def bounded_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    limit = max(0, int(max_chars))
    if len(text) <= limit:
        return text, False
    if limit <= 20:
        return text[:limit], True
    return text[: limit - 20].rstrip() + "\n[truncated by limit]", True


def mark_untrusted(kind: str, provenance: str, text: str) -> str:
    return f"[UNTRUSTED MCP {kind}: {provenance}]\n{text}\n[/UNTRUSTED MCP {kind}]"


def sanitize_resource_contents(
    raw_contents: Any,
    *,
    max_chars: int,
    provenance: str,
) -> tuple[list[dict[str, Any]], bool]:
    contents: list[dict[str, Any]] = []
    any_truncated = False
    remaining = max(0, int(max_chars))
    for item in raw_contents if isinstance(raw_contents, list) else []:
        if not isinstance(item, dict):
            continue
        clean = {k: v for k, v in item.items() if k not in {"text", "blob"}}
        uri = str(item.get("uri") or provenance)
        if isinstance(item.get("text"), str):
            text, truncated = bounded_text(item["text"], max_chars=remaining)
            any_truncated = any_truncated or truncated
            remaining = max(0, remaining - len(text))
            clean["text"] = mark_untrusted("RESOURCE", f"{provenance}; uri={uri}", text)
            clean["truncated"] = truncated
        elif isinstance(item.get("blob"), str):
            blob, truncated = bounded_text(item["blob"], max_chars=remaining)
            any_truncated = any_truncated or truncated
            remaining = max(0, remaining - len(blob))
            clean["blob"] = blob
            clean["truncated"] = truncated
            clean["untrusted"] = True
            clean["provenance"] = f"{provenance}; uri={uri}"
        else:
            clean["untrusted"] = True
            clean["provenance"] = f"{provenance}; uri={uri}"
        contents.append(clean)
        if remaining <= 0:
            any_truncated = True
            break
    return contents, any_truncated


def sanitize_prompt_messages(
    raw_messages: Any,
    *,
    max_chars: int,
    provenance: str,
) -> tuple[list[dict[str, Any]], bool]:
    messages: list[dict[str, Any]] = []
    any_truncated = False
    remaining = max(0, int(max_chars))
    for message in raw_messages if isinstance(raw_messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        clean: dict[str, Any] = {"role": role}
        if isinstance(content, dict):
            kind = content.get("type")
            if kind == "text" and isinstance(content.get("text"), str):
                text, truncated = bounded_text(content["text"], max_chars=remaining)
                any_truncated = any_truncated or truncated
                remaining = max(0, remaining - len(text))
                clean["content"] = {
                    **{k: v for k, v in content.items() if k != "text"},
                    "text": mark_untrusted("PROMPT", f"{provenance}; role={role}", text),
                    "truncated": truncated,
                }
            elif kind == "resource" and isinstance(content.get("resource"), dict):
                resources, truncated = sanitize_resource_contents(
                    [content["resource"]],
                    max_chars=remaining,
                    provenance=f"{provenance}; role={role}",
                )
                any_truncated = any_truncated or truncated
                clean["content"] = {
                    **{k: v for k, v in content.items() if k != "resource"},
                    "resource": resources[0] if resources else {},
                }
            else:
                clean["content"] = {**content, "untrusted": True, "provenance": provenance}
        else:
            text, truncated = bounded_text(str(content or ""), max_chars=remaining)
            any_truncated = any_truncated or truncated
            remaining = max(0, remaining - len(text))
            clean["content"] = {
                "type": "text",
                "text": mark_untrusted("PROMPT", f"{provenance}; role={role}", text),
                "truncated": truncated,
            }
        messages.append(clean)
        if remaining <= 0:
            any_truncated = True
            break
    return messages, any_truncated
