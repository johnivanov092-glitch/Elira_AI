"""Fixed source-to-answer contracts through the real coordinator and executor."""
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.application.code_agent.agent_loop import stream_code_agent
from webskill.application.code_agent.answer_contracts import explicit_web_check_requested


URL = "https://example.org/reference"
FACT = "The selected option is Alpha with 64 GB of memory."


@pytest.mark.parametrize("user_text,expected", [
    ("Проверь официальную документацию и покажи пример.", True),
    ("Check the official documentation.", True),
    ("Найди данные в интернете.", True),
    ("Не нужно проверять в интернете.", False),
    ("Не проверяй сайты. Объясни по приложенному тексту.", False),
    ('Переведи: «Проверь официальную документацию».', False),
    ("Покажи пример вызова web_search.", False),
    ("Прочитай локальную документацию из docs/README.md и объясни настройку.", False),
    ("Проверь документацию из приложенного README, без интернета.", False),
    ("Проверь официальную документацию. Не используй интернет: документ приложен.", False),
    ("Прочитай документацию проекта.", False),
    ("Прочитай приложенный README, затем проверь данные на официальном сайте.", True),
])
def test_only_explicit_source_check_requires_execution(user_text, expected):
    assert explicit_web_check_requested(user_text) is expected


@pytest.mark.parametrize("recovers", [False, True])
def test_fabricated_search_output_requires_real_execution_or_degraded_answer(tmp_path, monkeypatch, recovers):
    requests, turns = [], []
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200, text=FACT, url=url, encoding="utf-8",
            headers={"Content-Type": "text/plain"}, close=lambda: None)
    monkeypatch.setattr("requests.get", get)
    def chat(**kwargs):
        turns.append(1)
        assert len(turns) <= (3 if recovers else 2)
        if len(turns) == 1 or not recovers:
            return {"message": {"content": '[Исследование: web_search]\n{"result":{"url":"https://fake.invalid"}}'}}
        if len(turns) == 2:
            return {"message": {"tool_calls": [call("web_fetch", url=URL)]}}
        shown = next(m["content"] for m in reversed(kwargs["messages"]) if m["role"] == "tool")
        return {"message": {"content": "Alpha: 64 GB. " + re.search(r"\[\[source:[^\]]+\]\]", shown).group()}}
    events = list(stream_code_agent(user_message="Проверь официальную документацию и сообщи объём памяти.",
        project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536,
        base_tools=["capability_load", "web_search", "web_fetch"]))
    final = next(e for e in events if e["type"] == "final_response")
    assert final["answer_status"] == ("complete" if recovers else "degraded")
    assert "fake.invalid" not in final["text"]
    assert len(requests) == int(recovers)


def test_failed_source_does_not_attest_to_a_factual_answer(tmp_path, monkeypatch):
    requests, turns = [], []
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=429, url=url, headers={}, close=lambda: None)
    monkeypatch.setattr("requests.get", get)
    def chat(**kwargs):
        turns.append(1)
        assert len(turns) <= 4
        if len(turns) in (1, 3):
            return {"message": {"tool_calls": [call("web_fetch", url=URL)]}}
        return {"message": {"content": "Проверено: Alpha располагает 64 GB памяти."}}
    events = list(stream_code_agent(user_message="Проверь официальную документацию.", project_root=tmp_path,
        chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536,
        base_tools=["capability_load", "web_search", "web_fetch"]))
    final = next(e for e in events if e["type"] == "final_response")
    assert len(requests) == 2 and final["answer_status"] == "degraded"
    assert "64 GB" not in final["text"]


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


@pytest.mark.parametrize("retry", [False, True])
def test_http_evidence_reaches_next_turn_and_final_after_transient_failure(tmp_path, monkeypatch, retry):
    requests, turns = [], []
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    def get(url, **kwargs):
        requests.append(url)
        status = 503 if retry and len(requests) == 1 else 200
        return SimpleNamespace(status_code=status, text=FACT, url=url, encoding="utf-8",
            headers={"Content-Type": "text/plain"}, close=lambda: None)
    monkeypatch.setattr("requests.get", get)
    def chat(**kwargs):
        turns.append(1)
        if len(turns) <= (2 if retry else 1):
            return {"message": {"tool_calls": [call("web_fetch", url=URL)]}}
        shown = next(m["content"] for m in reversed(kwargs["messages"]) if m["role"] == "tool")
        assert FACT in shown
        source = re.search(r"\[\[source:[^\]]+\]\]", shown).group()
        return {"message": {"content": "Alpha располагает 64 GB памяти. " + source}}
    events = list(stream_code_agent(user_message="Проверь объём памяти по источнику.", project_root=tmp_path,
        chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536,
        base_tools=["capability_load", "web_search", "web_fetch"]))
    final = next(e for e in events if e["type"] == "final_response")
    assert final["answer_status"] == "complete" and len(requests) == (2 if retry else 1)
    assert any(s["quote"] == FACT and s["quote_verified"] for s in final["sources"])


def test_search_mode_can_load_tools_create_check_and_finish_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr("webskill.application.web.ssrf_guard.check_ssrf", lambda *a, **k: None)
    monkeypatch.setattr("requests.get", lambda url, **kwargs: SimpleNamespace(status_code=200, text=FACT,
        url=url, encoding="utf-8", headers={"Content-Type": "text/plain"}, close=lambda: None))
    checker = (
        "import json\nfrom pathlib import Path\n"
        "body=Path('selection.md').read_text(encoding='utf-8')\n"
        "ok='Alpha' in body and '64 GB' in body and 'https://example.org/reference' in body\n"
        "Path('verification.json').write_text(json.dumps({'checks':[{'name':'Source value and URL',"
        "'requirement_id':'report','passed':ok}]}),encoding='utf-8')\n"
    )
    argv = [sys.executable, "check.py"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    stages = iter([
        [call("web_fetch", url=URL)],
        [call("capability_load", group="project"), call("capability_load", group="shell")],
        [call("write_file", path="selection.md", content="Alpha: 64 GB. Source: " + URL + "\n"),
         call("write_file", path="check.py", content=checker)],
        # Checked the way agents check: run the checker and read its result.
        [call("run_bash", command=command)],
    ])
    seen = []
    def chat(**kwargs):
        seen.append({s["function"]["name"] for s in kwargs["tools"]})
        if len(seen) == 2:
            assert any(FACT in m.get("content", "") for m in kwargs["messages"] if m["role"] == "tool")
        batch = next(stages, None)
        assert len(seen) <= 5, "Artifact work must finish right after its check"
        return {"message": {"tool_calls": batch}} if batch else {"message": {"content": "Создан selection.md; значение и ссылка проверены."}}
    events = list(stream_code_agent(user_message="Создай selection.md с выбранным вариантом, объёмом памяти и URL источника.",
        project_root=tmp_path, chat_fn=chat, permission_mode="bypass", auto_remember=False, num_ctx=65536,
        base_tools=["capability_load", "web_search", "web_fetch"]))
    assert seen[0] - {"ask_user", "workflow_request"} == {"capability_load", "web_search", "web_fetch"}
    assert {"write_file", "run_bash"} <= seen[2]
    final = next(e for e in events if e["type"] == "final_response")
    assert final["answer_status"] == "complete", events
    assert json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))["checks"][0]["passed"]
    assert "Alpha: 64 GB" in (tmp_path / "selection.md").read_text(encoding="utf-8")
