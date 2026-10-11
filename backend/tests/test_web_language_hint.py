"""Language instructions do not rewrite successful search or transport evidence."""
import pytest
from webskill.application.code_agent.tools import _web
from webskill.infrastructure.search import web_search

@pytest.fixture(autouse=True)
def search(monkeypatch):
    monkeypatch.setattr(web_search, 'search_web', lambda query, *_args, **_kwargs: {
        'sources': [{'title': query, 'href': 'https://example.org/source', 'body': 'Snippet.'}],
        'engines_used': ['fixture'],
    })

@pytest.mark.parametrize('query,audience', [
    ('svelte vs react', 'global'),
    ('сравнение Svelte и React', 'global'),
    ('новости Казахстана', 'regional:Kazakhstan'),
])
def test_single_language_search_returns_evidence_without_language_error(query, audience):
    result = _web.tool_web_search(query=query, audience=audience)
    assert result['ok'] and result['sources']
    assert 'error' not in result and not result.get('partial')
    assert '[Языки поиска]' not in result['text']


def test_separate_calls_do_not_create_a_language_requirement():
    first = _web.tool_web_search(query='сравнение Svelte и React', audience='global')
    second = _web.tool_web_search(query='svelte vs react', audience='global')
    assert first['ok'] and second['ok']
    assert 'error' not in first and 'error' not in second


def test_regional_language_unchanged():
    result = _web.tool_web_search(query='новости Казахстана', audience='regional:Kazakhstan')
    assert result['ok'] and '[Языки поиска]' not in result['text']


def test_language_policy_preserves_long_result_without_injecting_hints():
    from webskill.search_language import search_with_language_policy

    sources = [{'url': 'https://example.org/source'}]
    original = {'ok': True, 'text': 'Source body. ' * 1000, 'sources': sources}
    result = search_with_language_policy(['English query'], 'global', lambda: original)
    assert result is original


@pytest.mark.parametrize('error', ['search_unavailable', 'argument_format', 'web_time_budget'])
def test_language_policy_preserves_original_error_and_evidence(error):
    from webskill.search_language import search_with_language_policy

    original = {'ok': False, 'partial': True, 'error': error, 'text': 'Original failure.',
                'results': [{'query': 'English query'}], 'sources': [{'url': 'https://example.org'}]}
    assert search_with_language_policy(['English query'], 'global', lambda: original) == original


def test_language_policy_is_idempotent_at_existing_call_boundaries():
    from webskill.search_language import search_with_language_policy

    original = {'ok': True, 'text': 'Original results.', 'results': [{'query': 'English query'}],
                'sources': [{'url': 'https://example.org'}], 'warnings': ['engine timed out']}
    first = search_with_language_policy(['English query'], 'global', lambda: original)
    second = search_with_language_policy(['English query'], 'global', lambda: first)
    assert second is first is original


def test_no_cache_search_never_writes_language_state(tmp_path):
    from webskill import context
    state = tmp_path / 'no-cache'
    context.configure(state, cache=False)
    for _ in range(3):
        result = _web.tool_web_search(query='svelte vs react', audience='global')
        assert result['ok'] and result['sources'] and 'error' not in result
    assert not state.exists()
