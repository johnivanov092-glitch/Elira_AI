from __future__ import annotations

from typing import Any

from app.application.chat.entrypoint_models import ChatAgentDeps


def _needs_file_generation(raw_user_input: str, deps: ChatAgentDeps) -> bool:
    query = (raw_user_input or "").lower()
    return any(trigger in query for trigger in deps.file_trigger_words + deps.file_trigger_excel)


def run_agent_stream_impl(
    *,
    deps: ChatAgentDeps,
    model_name: str,
    profile_name: str,
    user_input: str,
    session_id: str | None = None,
    use_memory: bool = True,
    use_library: bool = True,
    use_reflection: bool = False,
    history: list[Any] | None = None,
    num_ctx: int = 8192,
    use_web_search: bool = True,
    use_python_exec: bool = True,
    use_image_gen: bool = True,
    use_file_gen: bool = True,
    use_http_api: bool = True,
    use_sql: bool = True,
    use_screenshot: bool = True,
    use_encrypt: bool = True,
    use_archiver: bool = True,
    use_converter: bool = True,
    use_regex: bool = True,
    use_translator: bool = True,
    use_csv: bool = True,
    use_webhook: bool = True,
    use_plugins: bool = True,
):
    import time as _time

    agent_start = _time.monotonic()
    effective_agent_id = deps.resolve_effective_agent_id_func(profile_name=profile_name)
    bootstrap = deps.bootstrap_chat_run_func(
        user_input=user_input,
        history=history,
        max_history_pairs=deps.max_history_pairs,
        trim_history_func=deps.trim_history_func,
        strip_frontend_project_context_func=deps.strip_frontend_project_context_func,
        history_service=deps.history_service,
        planner_factory=deps.planner_factory,
        emit_run_started_func=deps.emit_agent_os_event_func,
        source_agent_id=effective_agent_id,
        profile_name=profile_name,
        model_name=model_name,
        session_id=str(session_id or ""),
        streaming=True,
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
    timeline = bootstrap.timeline
    tool_results = bootstrap.tool_results
    raw_user_input = bootstrap.raw_user_input
    planner_input = bootstrap.planner_input
    run = bootstrap.run

    try:
        yield deps.build_stream_phase_event_func(phase="planning", message="Planning...")

        execution = deps.prepare_chat_execution_func(
            planner_input=planner_input,
            model_name=model_name,
            plan_runner=bootstrap.planner.plan,
            use_memory=use_memory,
            use_library=use_library,
            use_web_search=use_web_search,
            is_memory_command_func=deps.is_memory_command_func,
            pick_model_for_route_func=deps.pick_model_for_route_func,
            run_id=run["run_id"],
            history_service=deps.history_service,
            extract_and_save_func=deps.extract_and_save_func,
            preflight_or_raise_func=deps.preflight_or_raise_func,
            agent_id=effective_agent_id,
            num_ctx=num_ctx,
            streaming=True,
            timeline=timeline,
            append_timeline_func=deps.append_timeline_func,
            log_auto_model_switch=True,
        )
        route = execution.route
        temporal = execution.temporal
        web_plan = execution.web_plan
        selected_tools = execution.selected_tools
        effective_model = execution.effective_model

        if deps.should_cache_func(planner_input, route) and not bootstrap.history:
            cached = deps.get_cached_func(planner_input, effective_model, profile_name)
            if cached:
                cached_hit = deps.prepare_cached_stream_hit_func(
                    cached_text=cached,
                    raw_user_input=raw_user_input,
                    timeline=timeline,
                    append_timeline_func=deps.append_timeline_func,
                    apply_identity_guard_func=deps.apply_identity_guard_func,
                    apply_provenance_guard_func=deps.apply_provenance_guard_func,
                    finalize_stream_success_func=deps.finalize_stream_success_func,
                    history_service=deps.history_service,
                    run_id=run["run_id"],
                    session_id=str(session_id or ""),
                    profile_name=profile_name,
                    model_name=effective_model,
                    route=route,
                    temporal=temporal,
                    web_plan=web_plan,
                    num_ctx=num_ctx,
                    agent_id=effective_agent_id,
                    source_agent_id=effective_agent_id,
                    selected_tools=selected_tools,
                    started_at=agent_start,
                    monotonic_now_func=_time.monotonic,
                )
                for token_event in deps.iter_text_stream_events_func(cached_hit.full_text):
                    yield token_event
                yield cached_hit.done_event
                return

        selected_tools_phase = deps.build_selected_tools_phase_event_func(selected_tools)
        if selected_tools_phase:
            yield selected_tools_phase

        yield deps.build_stream_phase_event_func(phase="thinking", message="Thinking...")

        prompt_bundle = deps.prepare_chat_prompt_func(
            profile_name=profile_name,
            raw_user_input=raw_user_input,
            planner_input=planner_input,
            route=route,
            selected_tools=selected_tools,
            tool_results=tool_results,
            timeline=timeline,
            use_reflection=use_reflection,
            temporal=temporal,
            web_plan=web_plan,
            disabled_skills=bootstrap.disabled_skills,
            has_rag=deps.has_rag,
            is_memory_command_func=deps.is_memory_command_func,
            get_relevant_context_func=deps.get_relevant_context_func,
            get_rag_context_func=deps.get_rag_context_func,
            collect_context_func=deps.collect_context_func,
            enrich_context_with_memory_func=deps.enrich_context_with_memory_func,
            build_prompt_func=deps.build_prompt_func,
            build_task_context_func=deps.build_task_context_func,
        )

        full_text = ""
        for token in deps.run_chat_stream_func(
            model_name=effective_model,
            profile_name=profile_name,
            user_input=prompt_bundle.prompt + deps.compose_human_style_rules_func(temporal),
            history=bootstrap.history,
            num_ctx=num_ctx,
            task_context=prompt_bundle.task_context,
        ):
            full_text += token
            yield {"token": token, "done": False}

        attachments = deps.get_and_clear_attachments_func()
        if attachments:
            full_text += attachments

        has_generated_files = deps.has_generated_file_attachments_func()
        should_reflect = (route in deps.reflection_routes) or use_reflection
        needs_file_generation = _needs_file_generation(raw_user_input, deps)

        if should_reflect and full_text.strip() and not has_generated_files:
            yield deps.build_stream_phase_event_func(phase="reflecting", message="Reflecting...")
            try:
                reflection = deps.run_reflection_loop_func(
                    model_name=effective_model,
                    profile_name=profile_name,
                    user_input=raw_user_input,
                    draft_text=full_text,
                    review_text="Improve.",
                    context=prompt_bundle.context_bundle,
                )
                refined = reflection.get("answer", "")
                if refined and refined != full_text:
                    full_text = refined
                    yield deps.build_stream_phase_event_func(
                        phase="reflection_replace",
                        full_text=refined,
                    )
            except Exception:
                pass

        if needs_file_generation:
            yield deps.build_stream_phase_event_func(
                phase="generating_file",
                message="Generating file...",
            )

        guarded = deps.apply_response_guards_func(
            raw_user_input=raw_user_input,
            text=full_text,
            timeline=timeline,
            use_python_exec=use_python_exec,
            use_file_gen=use_file_gen,
            append_timeline_func=deps.append_timeline_func,
            maybe_generate_files_func=deps.maybe_generate_files_func,
            maybe_auto_exec_python_func=deps.maybe_auto_exec_python_func,
        )
        full_text = guarded.text
        if guarded.changed:
            yield deps.build_stream_phase_event_func(
                phase="reflection_replace",
                full_text=full_text,
            )

        yield deps.finalize_stream_response_func(
            planner_input=planner_input,
            route=route,
            profile_name=profile_name,
            model_name=effective_model,
            raw_user_input=raw_user_input,
            full_text=full_text,
            selected_tools=selected_tools,
            temporal=temporal,
            web_plan=web_plan,
            identity_guard=guarded.identity_guard,
            provenance_guard=guarded.provenance_guard,
            history_service=deps.history_service,
            run_id=run["run_id"],
            session_id=str(session_id or ""),
            num_ctx=num_ctx,
            agent_id=effective_agent_id,
            source_agent_id=effective_agent_id,
            timeline=timeline,
            started_at=agent_start,
            monotonic_now_func=_time.monotonic,
            should_cache_func=deps.should_cache_func,
            set_cached_func=deps.set_cached_func,
            finalize_stream_success_func=deps.finalize_stream_success_func,
        )
    except Exception as exc:
        deps.finalize_chat_failure_func(
            history_service=deps.history_service,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=int((_time.monotonic() - agent_start) * 1000),
            streaming=True,
            num_ctx=num_ctx,
            agent_id=effective_agent_id,
            source_agent_id=effective_agent_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected_tools", []),
            history_payload={"ok": False, "error": str(exc)},
        )
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
