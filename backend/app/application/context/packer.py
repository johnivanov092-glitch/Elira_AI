from __future__ import annotations

import logging
from typing import Any

from app.application.context.usage import count_tokens


logger = logging.getLogger(__name__)


_PRIORITY = {
    "system": 0,
    "developer": 0,
    "user_input": 1,
    "recent_chat": 2,
    "rolling_summary": 3,
    "task_ledger": 4,
    "tools": 5,
    "rag": 6,
    "ocr": 7,
    "vision": 7,
    "code": 8,
    "documents": 8,
    "artifacts": 9,
}


def prioritize_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(blocks))
    indexed.sort(
        key=lambda pair: (
            0 if bool(pair[1].get("protected")) else 1,
            int(pair[1].get("priority", _PRIORITY.get(str(pair[1].get("category") or ""), 50))),
            pair[0],
        )
    )
    return [block for _, block in indexed]


def _head_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars < 80:
        return text[:max_chars]
    head = max_chars * 2 // 3
    tail = max_chars - head - 34
    return text[:head].rstrip() + "\n[… compressed; artifact retained …]\n" + text[-max(0, tail):].lstrip()


def compress_or_drop_blocks(
    blocks: list[dict[str, Any]],
    *,
    token_budget: int,
) -> dict[str, Any]:
    budget = max(0, int(token_budget))
    selected: list[dict[str, Any]] = []
    dropped: list[str] = []
    compressed: list[str] = []
    referenced_artifacts: list[str] = []
    used = 0

    for index, raw in enumerate(prioritize_blocks(blocks)):
        block = dict(raw)
        block_id = str(block.get("id") or f"block-{index}")
        content = str(block.get("content") or "")
        tokens = count_tokens(content)
        remaining = max(0, budget - used)
        protected = bool(block.get("protected"))
        if tokens <= remaining:
            block["content"] = content
            block["tokens"] = tokens
            selected.append(block)
            used += tokens
            continue
        if protected:
            block["content"] = content
            block["tokens"] = tokens
            block["overflow"] = True
            selected.append(block)
            used += tokens
            continue
        if remaining >= 64 and content:
            compacted = _head_tail(content, remaining * 4)
            artifact_ref = str(block.get("artifact_ref") or "").strip()
            if artifact_ref:
                compacted = _head_tail(compacted, max(80, remaining * 4 - len(artifact_ref) - 16))
                compacted += f"\n[Artifact: {artifact_ref}]"
                referenced_artifacts.append(artifact_ref)
            compacted_tokens = count_tokens(compacted)
            block["content"] = compacted
            block["tokens"] = compacted_tokens
            block["compressed"] = True
            selected.append(block)
            compressed.append(block_id)
            used += compacted_tokens
        else:
            dropped.append(block_id)

    return {
        "blocks": selected,
        "tokens": used,
        "budget": budget,
        "overflow": used > budget,
        "compressed_blocks": compressed,
        "dropped_blocks": dropped,
        "referenced_artifacts": referenced_artifacts,
    }


def pack_context(
    blocks: list[dict[str, Any]],
    *,
    safe_input_budget: int,
) -> dict[str, Any]:
    result = compress_or_drop_blocks(blocks, token_budget=safe_input_budget)
    result["warnings"] = []
    if result["compressed_blocks"]:
        result["warnings"].append("Some context blocks were compressed to fit the active profile.")
    if result["dropped_blocks"]:
        result["warnings"].append("Lower-priority context blocks were omitted; artifact references remain authoritative.")
    if result["overflow"]:
        result["warnings"].append(
            "Protected context exceeds the model input budget; the provider may reject this request."
        )
    logger.debug(
        "Context packed tokens=%s budget=%s compressed=%s dropped=%s artifacts=%s overflow=%s",
        result["tokens"], result["budget"], result["compressed_blocks"],
        result["dropped_blocks"], result["referenced_artifacts"], result["overflow"],
    )
    return result


def pack_message_context(
    messages: list[dict[str, Any]],
    *,
    safe_input_budget: int,
    recent_message_count: int = 12,
) -> dict[str, Any]:
    """Pack an OpenAI message list without breaking tool-call ordering."""
    last_user_index = max(
        (index for index, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    recent_start = max(0, len(messages) - max(1, int(recent_message_count)))
    blocks: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "user")
        explicit = str(message.get("context_category") or "").strip()
        has_tool_contract = role == "tool" or bool(message.get("tool_calls"))
        if explicit:
            category = explicit
        elif role in {"system", "developer"}:
            category = role
        elif index == last_user_index:
            category = "user_input"
        elif has_tool_contract:
            category = "tools"
        elif index >= recent_start:
            category = "recent_chat"
        else:
            category = "chat"
        blocks.append({
            "id": str(message.get("_msg_id") or f"message-{index}"),
            "category": category,
            "content": str(message.get("content") or ""),
            "protected": role in {"system", "developer"} or index == last_user_index or has_tool_contract,
            "original_index": index,
            "message": dict(message),
        })
    packed = pack_context(blocks, safe_input_budget=safe_input_budget)
    packed["preserve_message_order"] = True
    return packed


def build_final_messages(packed: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    blocks = list(packed.get("blocks") or [])
    if packed.get("preserve_message_order"):
        blocks.sort(key=lambda block: int(block.get("original_index") or 0))
    for block in blocks:
        category = str(block.get("category") or "chat")
        role = str(block.get("role") or ("system" if category in {"system", "developer", "rolling_summary", "task_ledger"} else "user"))
        content = str(block.get("content") or "").strip()
        original = block.get("message") if isinstance(block.get("message"), dict) else None
        if original is not None:
            message = dict(original)
            message["content"] = content
            message["context_category"] = category
            if content or message.get("tool_calls") or message.get("role") == "tool":
                messages.append(message)
        elif content:
            messages.append({"role": role, "content": content, "context_category": category})
    return messages
