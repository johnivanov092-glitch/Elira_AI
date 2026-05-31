"""
agents_service.py — thin routing facade.

All orchestration logic lives in:
  app/application/chat/service.py        (non-streaming)
  app/application/chat/stream_service.py (streaming)

This file keeps the backward-compatible public API (run_agent, run_agent_stream)
and re-exports the handful of symbols that tests or other modules import here.
"""
from __future__ import annotations

from typing import Any, Generator

# Re-exports used by tests
from app.application.chat.prompt_builder import (  # noqa: F401
    wants_explicit_datetime_answer as _wants_explicit_datetime_answer,
)
from app.application.planning.planner_v2_service import PlannerV2Service  # noqa: F401

# Service delegation
from app.application.chat.service import execute_chat_agent
from app.application.chat.stream_service import execute_chat_agent_stream

# _do_web_search used by test_web_multi_intent_runtime
from app.application.chat.service import _tl
from app.infrastructure.search.web_search import (
    do_web_search as _infra_do_web_search,
)


def _do_web_search(query, timeline, tool_results, web_plan=None):
    return _infra_do_web_search(query, timeline, tool_results, web_plan=web_plan, tl=_tl)


# ─── public API ──────────────────────────────────────────────────────────────


def run_agent(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
    agent_id=None,
    use_memory=True,
    use_library=True,
    use_reflection=False,
    history=None,
    num_ctx=8192,
    use_web_search=True,
    use_python_exec=True,
    use_image_gen=True,
    use_file_gen=True,
    use_http_api=True,
    use_sql=True,
    use_screenshot=True,
    use_encrypt=True,
    use_archiver=True,
    use_converter=True,
    use_regex=True,
    use_translator=True,
    use_csv=True,
    use_webhook=True,
    use_plugins=True,
):
    return execute_chat_agent(
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        agent_id=agent_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )


def run_agent_stream(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
    use_memory=True,
    use_library=True,
    use_reflection=False,
    history=None,
    num_ctx=8192,
    use_web_search=True,
    use_python_exec=True,
    use_image_gen=True,
    use_file_gen=True,
    use_http_api=True,
    use_sql=True,
    use_screenshot=True,
    use_encrypt=True,
    use_archiver=True,
    use_converter=True,
    use_regex=True,
    use_translator=True,
    use_csv=True,
    use_webhook=True,
    use_plugins=True,
) -> Generator[dict[str, Any], None, None]:
    yield from execute_chat_agent_stream(
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
