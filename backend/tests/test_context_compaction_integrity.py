"""Compaction must preserve the live user boundary and whole tool exchanges."""
from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent import loop_helpers
from app.application.code_agent.history import project_runtime_roles
from app.application.context.compaction import RUNTIME_BLOCK_KEY, maybe_compact
from app.infrastructure.llm.openai_compatible import _normalize_messages_for_request


PROFILE = {
    "reserved_output_tokens": 4096,
    "reserved_system_tokens": 4096,
    "safety_margin_tokens": 2048,
    "safe_input_budget": 120832,
    "compaction_thresholds": {
        "auto": {"percent": 75}, "strong": {"percent": 90},
        "critical": {"percent": 95},
    },
}


def exchange(*call_ids):
    assistant = {
        "role": "assistant", "content": "", "reasoning_content": "Keep this reasoning.",
        "tool_calls": [{"id": value, "type": "function", "function": {
            "name": "read_file", "arguments": {"path": value + ".txt"},
        }} for value in call_ids],
    }
    return [assistant, *({"role": "tool", "name": "read_file", "tool_call_id": value,
                         "content": "Output " + value} for value in call_ids)]


def no_inference(**kwargs):
    pytest.fail("Offline regression must not invoke inference")


@pytest.mark.parametrize("summary_ok", [True, False])
@pytest.mark.parametrize("batched", [True, False])
def test_prepare_preserves_real_user_and_complete_reasoning_tool_exchanges(monkeypatch, summary_ok, batched):
    current_user = {"role": "user", "content": "Прочитай исходные файлы и проверь результат."}
    messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Previous task."},
        {"role": "assistant", "content": "X" * (450000 if batched else 400000)},
        current_user,
    ]
    groups = [exchange("a", "b", "c")] if batched else [exchange(str(i)) for i in range(5)]
    for group in groups:
        messages.extend(group)
    guidance = {"role": "user", "content": "Runtime instruction", "_msg_id": "guidance",
                RUNTIME_BLOCK_KEY: "guidance"}
    messages.append(guidance)
    monkeypatch.setattr(loop_helpers, "summarize_history", lambda **kwargs: {
        "ok": summary_ok, "summary": "Summary" if summary_ok else "", "error": None,
    })
    packed, changed, usage = loop_helpers._prepare_messages_for_llm(
        messages, num_ctx=131072, model="local-model", chat_fn=no_inference,
        context_profile=PROFILE, pinned_message_ids={"guidance"},
    )
    assert changed
    provider = project_runtime_roles(packed)
    assert sum(message == current_user for message in provider) == 1
    assert guidance in packed
    assert "Runtime instruction" in provider[0]["content"]
    retained_calls = {call["id"] for message in provider for call in message.get("tool_calls", [])}
    retained_results = {message["tool_call_id"] for message in provider if message.get("role") == "tool"}
    assert retained_results == retained_calls
    for group in groups:
        if group[0] in provider:
            assert all(message in provider for message in group)
            assert provider.index(group[0]) < min(provider.index(message) for message in group[1:])
    assert groups[-1][0] in provider
    assert usage["percent"] < 95


def test_small_history_fallback_keeps_whole_batch_and_current_user():
    user = {"role": "user", "content": "Original user message."}
    group = exchange("a", "b", "c")
    messages = [{"role": "system", "content": "System"}, user, *group]
    packed, _ = maybe_compact(
        messages, num_ctx=131072, model="local-model", chat_fn=no_inference,
        summarize_fn=no_inference, threshold=0, keep_pairs=4, fallback_keep=2,
    )
    assert packed == messages


@pytest.mark.parametrize("summary_ok", [True, False])
def test_prepare_and_provider_pair_legacy_idless_results(monkeypatch, summary_ok):
    user = {"role": "user", "content": "Read all three files."}
    group = exchange("a", "b", "c")
    # The runtime supplies legacy results without IDs; the provider pairs them.
    for tool in group[1:]:
        tool.pop("tool_call_id")
    messages = [{"role": "system", "content": "System"},
                {"role": "user", "content": "Older task"},
                {"role": "assistant", "content": "X" * 450000}, user, *group]
    monkeypatch.setattr(loop_helpers, "summarize_history", lambda **kwargs: {
        "ok": summary_ok, "summary": "Summary" if summary_ok else "", "error": None,
    })
    packed, _, _ = loop_helpers._prepare_messages_for_llm(
        messages, num_ctx=131072, model="local-model", chat_fn=no_inference,
        context_profile=PROFILE,
    )
    wire = _normalize_messages_for_request(project_runtime_roles(packed))
    assert user in wire
    retained = next(message for message in wire if message.get("tool_calls"))
    assert retained["reasoning_content"] == group[0]["reasoning_content"]
    assert [json.loads(call["function"]["arguments"]) for call in retained["tool_calls"]] == [
        {"path": "a.txt"}, {"path": "b.txt"}, {"path": "c.txt"},
    ]
    assert [message["tool_call_id"] for message in wire if message["role"] == "tool"] == ["a", "b", "c"]
    assert [message["content"] for message in wire if message["role"] == "tool"] == ["Output a", "Output b", "Output c"]


def test_pinned_tool_result_retains_its_call_and_original_order():
    old_group = exchange("pinned")
    old_group[1]["_msg_id"] = "pinned-result"
    current = {"role": "user", "content": "Current instruction."}
    pending = exchange("pending")[0]
    messages = [{"role": "system", "content": "System"},
                {"role": "user", "content": "Old instruction."}, *old_group,
                {"role": "assistant", "content": "Old answer."}, current, pending]
    packed, _ = maybe_compact(
        messages, num_ctx=131072, model="local-model", chat_fn=no_inference,
        summarize_fn=lambda **kwargs: {"ok": True, "summary": "Summary"},
        threshold=0, keep_pairs=1, pinned_message_ids={"pinned-result"},
    )
    assert old_group[0] in packed and old_group[1] in packed
    assert packed.index(old_group[0]) < packed.index(old_group[1]) < packed.index(current)
    assert packed[-1] == pending


def test_protected_current_payload_over_budget_fails_without_inference(monkeypatch):
    messages = [{"role": "system", "content": "System"},
                {"role": "user", "content": "X" * 600000}, *exchange("large")]
    monkeypatch.setattr(loop_helpers, "summarize_history", no_inference)
    with pytest.raises(loop_helpers.ContextBudgetError):
        loop_helpers._prepare_messages_for_llm(
            messages, num_ctx=131072, model="local-model", chat_fn=no_inference,
            context_profile=PROFILE,
        )
