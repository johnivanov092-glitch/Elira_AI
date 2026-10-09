"""Process-local configuration for the standalone skill; no Elira imports."""
from __future__ import annotations

import contextvars
import hashlib
import os
from pathlib import Path

WEB_TOOL_RESULT_LLM_LIMIT = 12000
BROWSER_CHANGE_ACTIONS = frozenset({'fill', 'select', 'check', 'uncheck', 'click'})
_CURRENT_RUN_ID = contextvars.ContextVar('skill_run_id', default='')
_STATE = Path.cwd() / '.web-research'
_CACHE = True


def configure(state: Path, cache: bool = True) -> None:
    global _STATE, _CACHE
    _STATE = state.resolve()
    _CACHE = cache
    _CURRENT_RUN_ID.set(hashlib.sha256(str(_STATE).encode()).hexdigest()[:32])


def run_id() -> str:
    return _CURRENT_RUN_ID.get()


def data_file(name: str) -> Path:
    target = (_STATE / name).resolve()
    if not target.is_relative_to(_STATE):
        raise ValueError('state path escape')
    _STATE.mkdir(parents=True, exist_ok=True)
    return target


def run_persistence_policy(_run_id: str) -> dict:
    return {'rag': _CACHE}


def web_cache_write_allowed(policy: dict | None) -> bool:
    return isinstance(policy, dict) and policy.get('rag') is True


def active_server_ports() -> set[int]:
    return set()  # URL validator permits LAN/loopback by the existing user policy.


def _resolve_safe(root: Path, path: str) -> Path:
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f'file is outside the task workspace: {target}; workspace: {root}. Keep extraction JSON and its source.local_path original inside this workspace.')
    return target


def is_local_embed_enabled() -> bool:
    return os.environ.get('LOCAL_EMBED_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}


def embed_text(text: str):
    import requests
    from urllib.parse import urlsplit
    url = os.environ.get('LOCAL_EMBED_BASE_URL', 'http://192.168.88.15:8001/v1')
    host = urlsplit(url).hostname or ''
    import ipaddress
    try:
        local = ipaddress.ip_address(host).is_private
    except ValueError:
        local = host in {'localhost', 'ai-server'}
    if not local:
        raise ValueError('only the configured local embedding server is allowed')
    if not is_local_embed_enabled():
        return None
    try:
        response = requests.post(url.rstrip('/') + '/embeddings',
            headers={'Authorization': 'Bearer ' + os.environ.get('LOCAL_EMBED_API_KEY', 'local')},
            json={'input': text, 'model': os.environ.get('LOCAL_EMBED_MODEL', 'local-embed')},
            timeout=float(os.environ.get('LOCAL_EMBED_TIMEOUT_SECONDS', '30')))
        response.raise_for_status()
        vector = [float(x) for x in response.json()['data'][0]['embedding']]
        return vector if len(vector) == 1024 else None
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        return None
