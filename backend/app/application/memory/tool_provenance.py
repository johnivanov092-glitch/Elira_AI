"""Bind model-written memory to the current run, never to model-supplied labels."""
from __future__ import annotations

import logging
import re


_LOG = logging.getLogger(__name__)
_REMEMBER_PREFIX = re.compile(
    r"^(?:запомни|сохрани|remember|save)\b[,:]?\s+(?:что\s+|that\s+)?", re.I,
)


def _literal(text: str) -> str:
    return " ".join(text.strip().split()).rstrip(".")


def tool_memory_provenance(text: str, *, correction: bool = False) -> dict[str, str]:
    """Only an exact current user statement can receive user provenance.

    Paraphrases and observations stay useful, searchable agent notes. The
    original request is read from the existing server-owned journal, not tool
    arguments, attachments, assistant history, or a generated summary.
    """
    from app.application.code_agent.run_journal import RunJournal
    from app.application.code_agent.tools._shell import get_current_run_id

    result = {"source": "agent_note", "source_ref": ""}
    run_id = get_current_run_id()
    if not run_id:
        return result
    try:
        request = RunJournal.load(run_id).state.get("request")
    except (OSError, ValueError, TypeError):
        _LOG.debug("Memory origin unavailable for run %s; saving agent note", run_id)
        return result
    if not isinstance(request, dict):
        return result
    result["source_ref"] = f"run:{run_id}"
    user_text = request.get("memory_query")
    # user_message can be a model-generated delegate prompt. Only the raw
    # field supplied by the public user boundary establishes user origin.
    if not isinstance(user_text, str) or "[... truncated]" in user_text or "[REDACTED]" in user_text:
        return result
    literal = _literal(text)
    statements = {_literal(user_text), _literal(_REMEMBER_PREFIX.sub("", user_text.strip(), count=1))}
    if literal and literal in statements:
        result["source"] = "user_correction" if correction else "user_command"
    return result
