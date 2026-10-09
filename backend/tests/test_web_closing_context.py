"""A completed read is followed by the model's own next turn; runtime adds no closing cue.

A trailing user-role "[Следующий шаг: инструкция runtime, не выводи] …" block made
Qwen3.8 (vLLM :8011) treat its finished answer as reasoning, emit </think> and write
the whole answer a second time (A/B 2026-10-06: 8/8 doubled with the cue, 0/8 without).
"""
from __future__ import annotations

from app.application.code_agent.agent_loop import request_cancel, stream_code_agent
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.run_journal import RunJournal
from webskill.application.code_agent.tools import _web
from webskill.application.web_evidence.receipts import excerpt_sources, format_source
from webskill.infrastructure.search.web_runtime import PageFetchResult


URL = "https://example.org/first"
OTHER = "https://example.org/second"
BODY = "The observation was registered on 14 September 2015."
CUE_ID = "web-closing-context"


def _call(name, arguments, call_id="tool"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _no_cue(messages):
    assert not any(message.get("_msg_id") == CUE_ID for message in messages)
    assert not any("Следующий шаг: инструкция runtime" in str(message.get("content") or "")
                   for message in messages)








def test_read_handles_require_actual_verified_text(tmp_path):
    source = excerpt_sources(run_id="closing", tool="web_fetch", url=URL, text=BODY, fetched_at=1.0)[0]
    evidence = RunEvidence(sources=[source])
    marker_only = [{"role": "tool", "content": f"[[source:{source['id']}]]"}]
    assert evidence.read_source_handles(marker_only) == ()
    result = {"role": "tool", "content": format_source(source)}
    assert evidence.read_source_handles([result]) == (source["id"],)
