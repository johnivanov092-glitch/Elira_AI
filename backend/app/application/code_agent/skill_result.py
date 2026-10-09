"""Read an explicit skill receipt from a completed shell process.

This validates transport integrity, not the skill's network access or extraction.
No imports from mutable skills and no network calls belong to this boundary.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.application.code_agent.legacy_sources import valid_source

MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def read_skill_sources(stdout: str, project_root: Path) -> tuple[list[dict[str, Any]], str]:
    if len(stdout.encode('utf-8')) > MAX_RECEIPT_BYTES:
        return [], ''
    try:
        envelope = json.loads(stdout)
    except (ValueError, TypeError):
        return [], ''
    if not isinstance(envelope, dict) or envelope.get('elira_skill_result') != 1:
        return [], ''
    try:
        if envelope.get('ok') is not True:
            return [], ''
        path_value = envelope.get('record_path')
        if not isinstance(path_value, str) or not path_value:
            raise ValueError('record_path missing')
        path = (project_root / path_value).resolve()
        if not path.is_relative_to(project_root.resolve()):
            raise ValueError('receipt outside task workspace')
        with path.open('rb') as stream:
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ValueError('receipt too large')
        digest = hashlib.sha256(raw).hexdigest()
        if digest != envelope.get('record_sha256'):
            raise ValueError('receipt hash mismatch')
        record = json.loads(raw)
        result = record.get('result') if isinstance(record, dict) else None
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError('unsuccessful receipt')
        sources = result.get('sources', [])
        if not isinstance(sources, list) or len(sources) > 1024:
            raise ValueError('invalid sources array')
        if sources != envelope.get('sources', []):
            raise ValueError('receipt sources mismatch')
        if any(not valid_source(source) for source in sources):
            raise ValueError('invalid source record')
        return [{**source, 'attestation': 'skill', 'receipt_sha256': digest} for source in sources], ''
    except (OSError, ValueError, TypeError, RecursionError):
        return [], 'Skill source receipt rejected: invalid path, bytes or schema.'


def read_skill_job(stdout: str, project_root: Path, run_id: str) -> dict[str, Any]:
    """Adopt a CLI job result only if the durable runtime confirms its owner."""
    try:
        value = json.loads(stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(value, dict) or value.get("kind") != "job" or (value.get("backgrounded") is not True and value.get("action") != "start"):
        return {}
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not run_id:
        return {}
    from app.application.code_agent.tools._background_jobs import reconcile_job_records
    records, _ = reconcile_job_records()
    for record in records:
        if (record.get("pid") == pid and record.get("run_id") == run_id
                and Path(str(record.get("cwd") or "")).resolve() == project_root.resolve()):
            return {"kind": "job", "pid": pid, "job_id": str(record.get("job_id") or ""),
                    "status": str(record.get("status") or "failed"), "backgrounded": True}
    return {}
