"""A sibling ESR citation must not validate the audit's unsupported Stable period."""
import re

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from app.application.code_agent.delivery_session import build_continuation_kwargs
from app.application.code_agent.run_journal import RunJournal
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search.web_runtime import PageFetchResult


@pytest.mark.parametrize("corrects", [True, False])
@pytest.mark.parametrize("cited_without_quantity", [False, True])
def test_uncited_release_period_gets_one_correction_and_survives_resume(
    tmp_path, monkeypatch, corrects, cited_without_quantity,
):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    url = "https://developer.mozilla.org/en-US/docs/Mozilla/Firefox"
    monkeypatch.setattr(_web, "_fetch_one", lambda *args: PageFetchResult(
        text="Firefox Beta\nEvery four weeks, we take the features stable enough for a beta.\n"
             "Firefox ESR is a long-term support branch for enterprise use.",
        final_url=url, status_code=200,
    ))
    calls = 0
    source_id = ""

    def chat(**kwargs):
        nonlocal calls, source_id
        calls += 1
        if calls == 1:
            return {"message": {"content": "", "tool_calls": [{
                "id": "read-channels", "type": "function",
                "function": {"name": "web_fetch", "arguments": {"url": url}},
            }]}}
        if not source_id:
            tool_text = next(m["content"] for m in kwargs["messages"] if m["role"] == "tool")
            source_id = re.search(r"\[\[source:([^]]+)\]\]", tool_text).group(1)
        safe = f"ESR — ветка длительной поддержки для организаций. [[source:{source_id}]]"
        unsupported = (
            "- Стабильный канал получает новые версии каждые две недели. "
            f"[[source:{source_id}]]\n"
            if cited_without_quantity else
            "- Stable получает новую основную версию каждый релизный цикл (~4 недели).\n"
        )
        content = safe if corrects and calls > 2 else unsupported + f"- {safe}"
        return {"message": {"content": content, "tool_calls": []}}

    run_id = "web-cadence-acceptance"
    events = list(stream_code_agent(
        user_message="Проверь Mozilla: какие версии Firefox актуальны в стабильном канале и ESR?",
        project_root=tmp_path, model="test-model", chat_fn=chat,
        auto_remember=False, run_id=run_id,
    ))
    corrections = [e for e in events if e["type"] == "answer_format_correction"
                   and e.get("contract") == "web_cadence_citation"]
    assert len(corrections) == 1
    assert calls == 3
    final = next(e for e in events if e["type"] == "final_response")
    assert "~4 недели" not in final["text"]
    assert "каждые две недели" not in final["text"]
    assert final["answer_status"] == ("complete" if corrects else "degraded")
    assert all(c["claim_support"] == "not_assessed" for c in final.get("citations", []))
    state = RunJournal.load(run_id).state
    assert state["web_cadence_correction_sent"] is True
    if not corrects:
        assert state["status"] != "completed"
        resumed = list(stream_code_agent(**build_continuation_kwargs(run_id, chat_fn=chat)))
        assert calls == 4
        assert not any(e["type"] == "answer_format_correction" for e in resumed)
        final = next(e for e in resumed if e["type"] == "final_response")
        assert final["answer_status"] == "degraded"
