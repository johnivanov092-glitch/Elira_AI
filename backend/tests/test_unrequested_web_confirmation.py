"""No core-forced Web calls; explicit model-selected shell skills remain usable."""
import subprocess
import sys
import pytest
from app.application.code_agent.agent_loop import stream_code_agent

@pytest.mark.parametrize('draft', [
    'У меня нет доступа к актуальным новостям в реальном времени.',
    'У меня нет прямого доступа к интернету.',
    'Я не имею доступа к свежим новостям.',
    "I don't have access to current news.",
    'У меня нет доступа к этому локальному файлу.',
    'Привет! Рада тебя видеть.',
    'Лолита — твоя жена.',
    'MCP server start failed: unsupported RouterOS version',
    'command failed',
    'Не уверен, данных недостаточно.',
    'Перевод: «I do not have access to the internet».',
])
def test_model_prose_does_not_manufacture_builtin_web_actions(tmp_path, draft):
    captures = []
    def chat(**kwargs):
        captures.append(kwargs)
        return {'message': {'content': draft, 'tool_calls': []}}
    events = list(stream_code_agent(user_message='Обсудим результат.', project_root=tmp_path,
        chat_fn=chat, permission_mode='accept_edits', auto_remember=False))
    assert len(captures) == 1
    assert not any(e['type'] == 'tool_call' for e in events)
    assert not {'web_search', 'web_fetch', 'browser', 'http_api'} & {
        t['function']['name'] for t in captures[0]['tools']}
    assert next(e['text'] for e in events if e['type'] == 'final_response') == draft

@pytest.mark.parametrize('streaming', [False, True])
def test_model_selected_script_runs_without_unrequested_confirmation(tmp_path, streaming):
    script = tmp_path / 'read_fixture.py'
    script.write_text("print('Verified current event: https://example.org/news')", encoding='utf-8')
    responses = iter([
        {'message': {'content': '', 'tool_calls': [{'function': {'name': 'run_bash',
            'arguments': {'command': subprocess.list2cmdline([sys.executable, str(script)])}}}]}},
        {'message': {'content': 'Вот подтверждённое событие: https://example.org/news', 'tool_calls': []}},
    ])
    def chat(**_kwargs): return next(responses)
    def stream(**kwargs):
        response = chat(**kwargs)
        yield {'type': 'delta', 'content': response['message']['content']}
        yield {'type': 'message', 'response': response}
    events = list(stream_code_agent(user_message='Прочитай локальную фикстуру события.',
        project_root=tmp_path, chat_fn=chat, chat_stream_fn=stream if streaming else None,
        permission_mode='accept_edits', auto_remember=False))
    calls = [e for e in events if e['type'] == 'tool_call']
    assert len(calls) == 1 and calls[0]['tool'] == 'run_bash' and calls[0]['ok']
    assert not any(e['type'] in {'waiting_approval', 'workflow_request'} for e in events)
    final = next(e for e in events if e['type'] == 'final_response')
    assert 'https://example.org/news' in final['text']
    if streaming:
        assert all(e['answer_state'] == 'draft' for e in events if e['type'] == 'delta')
        assert final['answer_state'] == 'accepted'
