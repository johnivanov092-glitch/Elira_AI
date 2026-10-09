import hashlib
import json
import subprocess
import sys

import pytest

from app.application.code_agent.legacy_sources import make_source, format_source
from app.application.code_agent.skill_result import read_skill_sources
from app.application.code_agent.run_evidence import RunEvidence
from app.application.code_agent.tools._run import tool_run_bash


def receipt(tmp_path):
    source = make_source(run_id='skill-run', tool='web_fetch', url='https://example.org/a',
        status='excerpt', quote='A verified excerpt.', content_hash='a' * 64,
        offset=0, quote_verified=True)
    result = {'ok': True, 'sources': [source]}
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps({'result': result}), encoding='utf-8')
    return {**result, 'elira_skill_result': 1, 'record_path': str(path),
            'record_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_shell_receipt_reaches_evidence_only_after_presented_excerpt(tmp_path):
    envelope = receipt(tmp_path)
    script = tmp_path / 'emit.py'
    script.write_text('print(' + repr(json.dumps(envelope)) + ')', encoding='utf-8')
    result = tool_run_bash(tmp_path, command=subprocess.list2cmdline([sys.executable, str(script)]))
    assert result['ok'] and result['sources'][0]['attestation'] == 'skill'
    evidence = RunEvidence()
    evidence.record_tool_result(tool_name='run_bash', arguments={}, execution_status='ok',
        output=result, text_result=result['text'], state_changed=False)
    assert len(evidence.sources) == 1 and not evidence.presented_sources
    evidence.mark_sources_presented([{'role': 'tool', 'content': result['text']}])
    assert len(evidence.presented_sources) == 1
    assert evidence.citations('“A verified excerpt.” ' + format_source(result['sources'][0]))[0]['status'] == 'matched'


@pytest.mark.parametrize('change', ['bytes', 'path', 'sources', 'schema', 'failed'])
def test_forged_or_failed_receipt_cannot_publish_sources(tmp_path, change):
    envelope = receipt(tmp_path)
    if change == 'bytes':
        (tmp_path / 'receipt.json').write_text('{}', encoding='utf-8')
    elif change == 'path':
        envelope['record_path'] = str(tmp_path.parent / 'receipt.json')
    elif change == 'sources':
        envelope['sources'][0]['quote'] = 'Fabricated'
    elif change == 'schema':
        envelope['elira_skill_result'] = 2
    else:
        envelope['ok'] = False
    assert read_skill_sources(json.dumps(envelope), tmp_path)[0] == []


def test_plain_json_is_not_a_skill_receipt(tmp_path):
    envelope = receipt(tmp_path)
    envelope.pop('elira_skill_result')
    assert read_skill_sources(json.dumps(envelope), tmp_path) == ([], '')


def test_nonzero_process_cannot_attest_receipt(tmp_path):
    envelope = receipt(tmp_path)
    script = tmp_path / 'emit.py'
    script.write_text('print(' + repr(json.dumps(envelope)) + ')\nraise SystemExit(1)', encoding='utf-8')
    result = tool_run_bash(tmp_path, command=subprocess.list2cmdline([sys.executable, str(script)]))
    assert not result['ok'] and not result.get('sources')


def test_redaction_expansion_keeps_skill_receipt_valid_without_claiming_verbatim(tmp_path):
    from webskill.application.web_evidence.receipts import make_source as skill_source
    from app.application.code_agent.legacy_sources import valid_source

    quote = ("--token x " + "z" * 1500)[:1500]
    redacted = skill_source(run_id='skill-run', tool='web_fetch', url='https://example.org/a',
        status='excerpt', quote=quote, content_hash='a' * 64, offset=0, quote_verified=True)
    assert len(redacted['quote']) <= 1500
    assert '[REDACTED]' in redacted['quote']
    assert redacted['quote_verified'] is False
    assert valid_source(redacted)
    envelope = receipt(tmp_path)
    envelope['sources'].append(redacted)
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps({'result': {'ok': True, 'sources': envelope['sources']}}), encoding='utf-8')
    envelope['record_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    accepted, error = read_skill_sources(json.dumps(envelope), tmp_path)
    assert not error and len(accepted) == 2
    assert accepted[0]['quote_verified'] is True
    assert accepted[1]['quote_verified'] is False
