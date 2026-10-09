"""Large native web batches retain receipts without bypassing context guards."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.loop_helpers import (
    ContextBudgetError, _prepare_messages_for_llm,
)
from app.application.code_agent.run_evidence import RunEvidence
from webskill.application.code_agent.tools._web import tool_web_fetch, tool_web_search
from app.application.context.profile import resolve_context_window
from app.application.context.usage import get_context_usage
from webskill.application.web_evidence.receipts import valid_source
from webskill.infrastructure.search.web_runtime import PageFetchResult


QUERIES = [f"documentation-query-{index}" for index in range(5)]
URLS = [f"https://example.org/documentation/{index}" for index in range(5)]








@pytest.mark.parametrize("num_ctx", [32768, 8192])
def test_small_windows_compact_history_without_oversized_model_request(num_ctx):
    profile = resolve_context_window(num_ctx, live=False)
    calls, audit = [], []
    history = [{"role": "system", "content": "Keep the user request."},
               {"role": "user", "content": "Read the documentation.", "_msg_id": "request"}]
    history.extend({"role": "user" if index % 2 == 0 else "assistant",
                    "content": "old documentation " * 1000} for index in range(40))
    history.extend([{"role": "assistant", "content": "Recent read."},
                    {"role": "tool", "name": "web_fetch", "content": "Current excerpt. " * 30}])

    def chat(**kwargs):
        calls.append(deepcopy(kwargs))
        return {"message": {"content": "Earlier documents summarized."}}

    packed, compacted, usage = _prepare_messages_for_llm(
        history, num_ctx=num_ctx, model="offline-model", chat_fn=chat, context_profile=profile,
        pinned_message_ids={"request"}, audit_sink=audit.append,
    )
    assert compacted and audit
    assert any(message.get("_msg_id") == "request" for message in packed)
    assert any("Current excerpt." in str(message.get("content")) for message in packed)
    assert usage["current_tokens"] <= profile["safe_input_budget"]
    assert usage["percent"] < profile["compaction_thresholds"]["critical"]["percent"]
    for call in calls:
        assert get_context_usage(call["messages"], ctx_size=num_ctx)["current_tokens"] < num_ctx












@pytest.mark.parametrize("num_ctx", [32768, 8192])
def test_oversized_protected_request_cannot_reach_a_small_window_model(num_ctx):
    profile = resolve_context_window(num_ctx, live=False)
    model_calls = []
    messages = [{"role": "system", "content": "Mandatory instructions. " * num_ctx},
                {"role": "user", "content": "Current request.", "_msg_id": "request"},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_fetch"}}]},
                {"role": "tool", "name": "web_fetch", "content": "page data " * 12000}]
    with pytest.raises(ContextBudgetError):
        _prepare_messages_for_llm(
            messages, num_ctx=num_ctx, model="offline-model", context_profile=profile,
            chat_fn=lambda **kwargs: model_calls.append(kwargs), pinned_message_ids={"request"},
        )
    assert model_calls == []
