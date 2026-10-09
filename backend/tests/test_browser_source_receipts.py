from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.run_evidence import RunEvidence
from webskill.application.code_agent.tools import _web
from webskill.application.web_evidence.receipts import valid_source


def _rendered(result: tuple, **kwargs) -> dict:
    with (
        patch.object(_web, "_browser_render", return_value=result),
        patch.object(_web, "_current_run_id", return_value="browser-source-run"),
        patch("app.application.code_agent.tools._run.active_server_ports", return_value=set()),
        patch("webskill.application.web.ssrf_guard.check_ssrf", return_value=None),
    ):
        return _web.tool_browser(url="https://example.org/requested", **kwargs)


@pytest.mark.parametrize("final_url", ["https://example.org/actual#section", "http://192.168.1.8/docs"])
def test_successful_browser_sources_use_observed_dom_url_and_keep_verifier_contract(final_url: str) -> None:
    body = "Observed documentation. " * 90
    title = "Rendered title"
    viewport = {"checked": True, "width": 375, "no_hoverflow": True}
    out = _rendered((title, final_url, body, 1, viewport, 200),
                    actions=[{"fill": "Query", "value": "ACTION_ECHO_NOT_DOM"}], viewport="mobile")

    assert out["ok"] is True and out["verifier"] is True and out["interacted"] is True
    assert out["viewport"] == viewport
    assert out["evidence"] == f"TITLE: {title}\n{body.strip()}"
    assert "ACTION_ECHO_NOT_DOM" in out["text"]
    assert "ACTION_ECHO_NOT_DOM" not in out["evidence"]
    assert out["text"].startswith(f"[browser: {final_url}]\n")
    assert "<<<DATA" in out["text"] and f"TITLE: {title}" in out["text"]
    assert len(out["sources"]) > 1
    assert "".join(source["quote"] for source in out["sources"]) == body.strip()
    for source in out["sources"]:
        assert valid_source(source)
        assert source["url"] == final_url and source["title"] == title
        assert source["tool"] == "browser" and source["origin_run_id"] == "browser-source-run"
        assert source["status"] == "excerpt" and source["quote_verified"] is True
        assert "ACTION_ECHO_NOT_DOM" not in source["quote"]
        assert f"[[source:{source['id']}]]" in out["text"]


@pytest.mark.parametrize("rendered,actions", [
    (("Error", "https://example.org/error", "Service unavailable", 0, None, 503), None),
    (("Empty", "https://example.org/empty", "   ", 0, None, 200), None),
    (("Form", "https://example.org/form", "No change", 0, None, 200), [{"click": "Submit"}]),
])
def test_failed_empty_or_incomplete_browser_never_issues_successful_excerpts(rendered, actions) -> None:
    out = _rendered(rendered, actions=actions)
    assert out["ok"] is False
    assert out.get("sources", []) == []
    if actions:
        assert out["error"] == "browser_actions_incomplete"
        assert out["verifier"] is True and out["evidence"] == "TITLE: Form\nNo change"


def test_browser_sources_use_existing_evidence_and_restore_with_links() -> None:
    out = _rendered(("Docs", "https://example.org/final", "Supported option: local GPU.", 0, None, 200))
    evidence = RunEvidence()
    evidence.record_tool_result(tool_name="browser", arguments={"url": "https://example.org/requested"},
                                execution_status="ok", output=out, text_result=out["text"], state_changed=False)
    assert not evidence.has_external_source  # Collection is not presentation.
    evidence.mark_sources_presented([{"role": "tool", "content": out["text"]}])
    assert evidence.has_external_source
    source_id = out["sources"][0]["id"]
    answer = f"Supported option: local GPU. [[source:{source_id}]]"
    assert evidence.citations(answer)[0]["status"] == "matched"

    restored = RunEvidence(sources=json.loads(json.dumps(evidence.sources)))
    context = restored.source_context([])
    assert "https://example.org/final" in context
    assert "Supported option: local GPU." in context
    restored.mark_sources_presented([{"role": "tool", "content": context}])
    assert restored.citations(answer)[0]["status"] == "matched"
