"""Standalone web skill CLI. Each operation records its actual result and hash."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from webskill import context
from webskill.application.code_agent.tools import _web


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=Path.cwd() / '.web-research')
    p.add_argument('--no-cache', action='store_true')
    sub = p.add_subparsers(dest='action', required=True)
    search = sub.add_parser('search')
    search.add_argument('--query', action='append', required=True)
    search.add_argument('--max-results', type=int, default=5)
    search.add_argument('--categories', default='')
    search.add_argument('--time-range', default='')
    search.add_argument('--audience', default='')
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--url', action='append', required=True)
    fetch.add_argument('--max-chars', type=int, default=8000)
    fetch.add_argument('--find', default='')
    fetch.add_argument('--store', action='store_true')
    fetch.add_argument('--force-refresh', action='store_true')
    fetch.add_argument('--extraction-path', default='')
    query = sub.add_parser('query')
    query.add_argument('--query', required=True)
    query.add_argument('--doc-id', default='')
    query.add_argument('--top-k', type=int, default=6)
    browser = sub.add_parser('browser')
    browser.add_argument('--url', required=True)
    browser.add_argument('--wait-selector')
    browser.add_argument('--actions-file', type=Path)
    browser.add_argument('--viewport')
    browser.add_argument('--max-chars', type=int, default=8000)
    browser.add_argument('--force-refresh', action='store_true')
    http = sub.add_parser('http')
    http.add_argument('--url', required=True)
    http.add_argument('--method', default='GET')
    http.add_argument('--headers-file', type=Path)
    http.add_argument('--body-file', type=Path)
    shot = sub.add_parser('screenshot')
    shot.add_argument('--url', required=True)
    shot.add_argument('--output', type=Path, required=True)
    shot.add_argument('--width', type=int, default=1280)
    shot.add_argument('--height', type=int, default=800)
    shot.add_argument('--full-page', action='store_true')
    check = sub.add_parser('verify')
    check.add_argument('--answer-file', type=Path, required=True)
    sub.add_parser('status')
    return p


def read_json(path: Path | None):
    if path is None:
        return None
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('JSON argument exceeds 1 MiB')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def verify(answer_file: Path) -> dict:
    from webskill.application.code_agent.answer_contracts import web_source_citation_violations
    from webskill.application.web_evidence.receipts import valid_source
    answer = answer_file.read_text(encoding='utf-8-sig')
    sources = []
    for path in context.data_file('operations').glob('*.json'):
        row = read_json(path)
        for source in row.get('result', {}).get('sources', []):
            if valid_source(source):
                sources.append(source)
    read = {s['url'] for s in sources if s.get('quote_verified') is True}
    issues = web_source_citation_violations(answer, read_source_urls=read,
                                           known_source_urls={s['url'] for s in sources})
    return {'ok': bool(read) and not issues, 'read_urls': sorted(read),
            'answer_sha256': hashlib.sha256(answer.encode('utf-8')).hexdigest(),
            'issues': [{'url': i.url, 'reason': i.reason, 'block_index': i.block_index} for i in issues],
            'scope': 'skill-recorded provenance only; semantic accuracy is not certified'}


def execute(args) -> dict:
    if args.action == 'search':
        return _web.tool_web_search(queries=args.query, top_k=args.max_results,
            categories=args.categories, time_range=args.time_range, audience=args.audience)
    if args.action == 'fetch':
        return _web.tool_web_fetch(url=args.url[0] if len(args.url) == 1 else '',
            urls=args.url if len(args.url) > 1 else None, max_chars=args.max_chars,
            find=args.find, store=args.store, force_refresh=args.force_refresh,
            extraction_path=args.extraction_path, project_root=Path.cwd())
    if args.action == 'query':
        return _web.tool_web_query(query=args.query, doc_id=args.doc_id, top_k=args.top_k)
    if args.action == 'browser':
        actions = read_json(args.actions_file)
        if actions is not None and not isinstance(actions, list):
            raise ValueError('actions file must contain a JSON array')
        return _web.tool_browser(url=args.url, wait_selector=args.wait_selector, actions=actions,
            viewport=args.viewport, max_chars=args.max_chars, force_refresh=args.force_refresh)
    if args.action == 'http':
        from webskill.http import http_request
        return http_request(args.url, method=args.method, headers=read_json(args.headers_file),
                            body=read_json(args.body_file))
    if args.action == 'screenshot':
        from playwright.sync_api import sync_playwright
        from webskill.application.web.ssrf_guard import check_ssrf
        if check_ssrf(args.url):
            raise ValueError('invalid HTTP(S) URL')
        if not (200 <= args.width <= 4096 and 200 <= args.height <= 4096):
            raise ValueError('viewport dimensions must be between 200 and 4096')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': args.width, 'height': args.height})
                response = page.goto(args.url, wait_until='domcontentloaded', timeout=30000)
                page.screenshot(path=str(args.output), full_page=args.full_page)
                return {'ok': response is not None and response.ok, 'url': page.url,
                        'title': page.title(), 'path': str(args.output.resolve()),
                        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}
            finally:
                browser.close()
    if args.action == 'verify':
        return verify(args.answer_file)
    from webskill.core.web_engines import searxng_url
    return {'ok': True, 'searxng_url': searxng_url(), 'state_dir': str(args.state_dir.resolve()),
            'python': sys.executable, 'skill_root': str(Path(__file__).resolve().parent)}


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    import os
    config_path = Path(__file__).resolve().with_name('config.json')
    if config_path.is_file():
        config = read_json(config_path)
        if isinstance(config, dict) and isinstance(config.get('searxng_url'), str):
            os.environ.setdefault('SEARXNG_URL', config['searxng_url'])
    args = parser().parse_args()
    context.configure(args.state_dir, cache=not args.no_cache)
    started = time.time()
    try:
        result = execute(args)
    except Exception as exc:
        from webskill.core.redaction import redact_text
        result = {'ok': False, 'error': type(exc).__name__, 'text': redact_text(str(exc))[:1000]}
    # Evidence belongs to this task's files; it is not written into Elira memory.
    if not args.no_cache:
        from webskill.core.redaction import redact_text
        payload = json.dumps({'action': args.action, 'started_at': started, 'finished_at': time.time(),
                              'result': result}, ensure_ascii=False, indent=2)
        path = context.data_file('operations') / (str(time.time_ns()) + '-' + uuid.uuid4().hex[:8] + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + '\n', encoding='utf-8', newline='\n')
        result = {**result, 'record_path': str(path), 'record_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
