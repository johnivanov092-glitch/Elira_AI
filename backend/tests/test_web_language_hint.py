"""Language requirements at the mutable search boundary; failures remain visible."""
import pytest
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search import web_search

HINT = "[Языки поиска]"

@pytest.fixture(autouse=True)
def search(monkeypatch):
    monkeypatch.setattr(web_search, 'search_web', lambda query, *_args, **_kwargs: {
        'sources': [{'title': query, 'href': 'https://example.org/source', 'body': 'Snippet.'}],
        'engines_used': ['fixture'],
    })

@pytest.mark.parametrize('query,missing', [
    ('world war z perks', 'запросы на русском'),
    ('новости Казахстана', 'запросы на английском'),
])
def test_single_language_reminder_names_missing_language(query, missing):
    result = _web.tool_web_search(query=query)
    assert missing in result['text']
    assert 'регион' in result['text'].lower()

def test_mixed_queries_need_no_language_reminder():
    result = _web.tool_web_search(queries=['world war z perks', 'прокачка перков wwz'])
    assert HINT not in result['text']

def test_one_reminder_across_repeated_single_language_searches():
    results = [_web.tool_web_search(query=q) for q in ['world war z perks', 'world war z xp']]
    assert sum(HINT in r['text'] for r in results) == 1

def test_no_new_reminder_after_both_languages_used():
    first = _web.tool_web_search(query='прокачка перков world war z')
    second = _web.tool_web_search(query='world war z perks leveling')
    assert HINT in first['text']
    assert HINT not in second['text']

def test_declared_global_topic_needs_both_languages_across_task():
    first = _web.tool_web_search(queries=['svelte vs react 2026', 'svelte 5 runes'], audience='global')
    assert first['ok'] is False and first['error'] == 'audience_languages'
    assert 'запросы на русском' in first['text']
    second = _web.tool_web_search(queries=['svelte vs react 2026', 'сравнение svelte и react'], audience='global')
    third = _web.tool_web_search(query='site:svelte.dev runes', audience='global')
    assert second['ok'] and third['ok']

def test_declared_regional_topic_may_stay_in_one_language():
    result = _web.tool_web_search(query='новости Казахстана за неделю', audience='regional:Казахстан')
    assert result['ok']

def test_language_rejections_remain_bounded():
    results = [_web.tool_web_search(query='svelte vs react', audience='global') for _ in range(3)]
    assert [r['ok'] for r in results] == [False, False, True]


def test_no_cache_cannot_create_rejection_loop_or_state(tmp_path):
    from webskill import context
    state = tmp_path / 'no-cache'
    context.configure(state, cache=False)
    for _ in range(3):
        assert _web.tool_web_search(query='world war z', audience='global')['ok']
    assert not state.exists()


def test_corrupt_language_state_is_advisory(tmp_path):
    from webskill import context
    directory = context.data_file('search-language')
    directory.mkdir()
    (directory / 'bad.json').write_text('{', encoding='utf-8')
    result = _web.tool_web_search(query='world war z', audience='global')
    assert result['ok']
    assert 'недоступно' in result['text']


def test_discarded_sixth_query_cannot_satisfy_language_requirement():
    result = _web.tool_web_search(queries=['english ' + str(i) for i in range(5)] + ['русский'], audience='global')
    assert not result['ok'] and result['error'] == 'audience_languages'


def test_rejection_budget_survives_process_restart(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    skill = Path(__file__).resolve().parents[2] / 'skills/web-research'
    code = "from pathlib import Path; import json,sys; from webskill import context; from webskill.search_language import search_with_language_policy; context.configure(Path(sys.argv[1])); print(json.dumps(search_with_language_policy(['english'], 'global', lambda: {'ok': True, 'text': 'results'})))"
    rows = [json.loads(subprocess.check_output([sys.executable, '-c', code, str(tmp_path / 'shared-state')], cwd=skill, encoding='utf-8')) for _ in range(3)]
    assert [r['ok'] for r in rows] == [False, False, True]
