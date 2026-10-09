"""John's rule (2026-10-06): a general topic is searched in Russian and English.

One reminder per run, kept in the search result when every query so far uses
one script; the model decides whether the topic is regional.
"""
from copy import deepcopy

import pytest

from _runtime_roles import user_texts
from app.application.code_agent import agent_loop
from app.application.code_agent.agent_loop import _web_language_hint
from app.infrastructure.search import web_search

HINT = "[Языки поиска]"


def test_hint_names_the_missing_language_and_stays_silent_for_mixed_queries():
    english = _web_language_hint([{"queries": ["world war z perks", "site:steamcommunity.com wwz"]}])
    assert "только на английском" in english and "запросы на русском" in english
    russian = _web_language_hint([{"queries": ["новости Казахстана за неделю"]}])
    assert "только на русском" in russian and "запросы на английском" in russian
    assert "конкретной стране или регионе" in russian  # regional topics may stay in one language
    assert _web_language_hint([{"queries": ["world war z perks"]}, {"queries": ["прокачка перков wwz"]}]) == ""
    assert _web_language_hint([]) == ""


@pytest.fixture
def search(tmp_path, monkeypatch):
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr("app.infrastructure.llm.openai_compatible.server_context_window",
                        lambda *, fresh=True: 65536)
    monkeypatch.setattr(web_search, "search_web", lambda query, *_args, **_kwargs: {
        "sources": [{"title": f"Result for {query}", "href": f"https://example.org/{abs(hash(query)) % 997}",
                     "body": "Snippet."}],
        "engines_used": ["fixture"],
    })


def _run(tmp_path, queries):
    captures = []

    def chat(**kwargs):
        captures.append(deepcopy(kwargs["messages"]))
        index = len(captures) - 1
        if index < len(queries):
            return {"message": {"content": "", "tool_calls": [{
                "id": f"search-{index}", "function": {"name": "web_search", "arguments": {"query": queries[index]}},
            }]}}
        return {"message": {"content": "Ответ по найденному.", "tool_calls": []}}

    list(agent_loop.stream_code_agent(
        user_message="Как быстро прокачать перки в World War Z?", project_root=tmp_path, num_ctx=65536,
        base_tools=["web_search", "web_fetch"], permission_mode="bypass", auto_remember=False, chat_fn=chat))
    return captures


def _search_results_with_hint(messages):
    return [message for message in messages
            if message.get("role") == "tool" and HINT in str(message.get("content") or "")]


def test_one_reminder_kept_in_the_first_single_language_search_result(tmp_path, search):
    captures = _run(tmp_path, ["world war z perks leveling", "world war z xp farming"])
    assert not _search_results_with_hint(captures[0])
    assert len(_search_results_with_hint(captures[1])) == 1  # in the first search result
    assert len(_search_results_with_hint(captures[-1])) == 1  # stays in history, never repeated
    assert all(HINT not in text for messages in captures for text in user_texts(messages))


def test_no_reminder_once_both_languages_are_used(tmp_path, search):
    captures = _run(tmp_path, ["прокачка перков world war z", "world war z perks leveling"])
    assert len(_search_results_with_hint(captures[1])) == 1  # first search was Russian-only
    assert len(_search_results_with_hint(captures[-1])) == 1  # the English search adds nothing


def _run_calls(tmp_path, calls):
    """calls: web_search arguments per model turn; returns the tool events of the run."""
    turns = []

    def chat(**kwargs):
        index = len(turns)
        turns.append(index)
        if index < len(calls):
            return {"message": {"content": "", "tool_calls": [{
                "id": f"search-{index}", "function": {"name": "web_search", "arguments": calls[index]}}]}}
        return {"message": {"content": "Ответ по найденному.", "tool_calls": []}}

    events = list(agent_loop.stream_code_agent(
        user_message="Сравни Svelte и React", project_root=tmp_path, num_ctx=65536,
        base_tools=["web_search", "web_fetch"], permission_mode="bypass", auto_remember=False, chat_fn=chat))
    calls = [event for event in events if event.get("type") == "tool_call" and event.get("tool") == "web_search"]
    for call in calls:
        call["status"] = call.get("execution_status") or ("ok" if call.get("ok") else "failed")
    return calls


def test_declared_global_topic_needs_both_languages_across_the_run(tmp_path, search):
    calls = _run_calls(tmp_path, [
        {"queries": ["svelte vs react 2026", "svelte 5 runes"], "audience": "global"},  # English only → rejected
        {"queries": ["svelte vs react 2026", "сравнение svelte и react"], "audience": "global"},  # both → runs
        {"query": "site:svelte.dev runes", "audience": "global"},  # the run already has both languages
    ])
    assert [call["status"] for call in calls] == ["rejected", "ok", "ok"]
    assert calls[0]["error"] == "audience_languages" and not calls[0]["dispatched"]
    assert "запросы на русском" in calls[0]["result"] and 'audience="regional:<страна>"' in calls[0]["result"]


def test_declared_regional_topic_may_stay_in_one_language(tmp_path, search):
    calls = _run_calls(tmp_path, [{"queries": ["новости Казахстана за неделю"], "audience": "regional:Казахстан"}])
    assert [call["status"] for call in calls] == ["ok"]


def test_rejections_are_bounded_so_the_run_never_loops(tmp_path, search):
    english = {"queries": ["svelte vs react"], "audience": "global"}
    calls = _run_calls(tmp_path, [english, english, english])
    assert [call["status"] for call in calls] == ["rejected", "rejected", "ok"]


def test_undeclared_environment_keeps_the_single_reminder(tmp_path, search):
    captures = _run(tmp_path, ["world war z perks leveling", "world war z xp farming"])
    assert len(_search_results_with_hint(captures[-1])) == 1
