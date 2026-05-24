from __future__ import annotations

from typing import Any

from app.application.chat.entrypoint_models import ChatAgentDeps


def run_agent_impl(
    *,
    deps: ChatAgentDeps,
    model_name: str,
    profile_name: str,
    user_input: str,
    session_id: str | None = None,
    agent_id: str | None = None,
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
) -> dict[str, Any]:
    import time as _time

    agent_start = _time.monotonic()
    registry_agent = None
    if agent_id and deps.resolve_agent_func is not None:
        try:
            registry_agent = deps.resolve_agent_func(agent_id=agent_id)
            if registry_agent:
                if registry_agent.get("system_prompt"):
                    profile_name = registry_agent.get("name_ru") or profile_name
                if registry_agent.get("model_preference"):
                    model_name = registry_agent["model_preference"]
        except Exception:
            pass

    effective_agent_id = deps.resolve_effective_agent_id_func(
        agent_id=agent_id,
        profile_name=profile_name,
        registry_agent=registry_agent,
    )
    source_agent_id = effective_agent_id
    bootstrap = deps.bootstrap_chat_run_func(
        user_input=user_input,
        history=history,
        max_history_pairs=deps.max_history_pairs,
        trim_history_func=deps.trim_history_func,
        strip_frontend_project_context_func=deps.strip_frontend_project_context_func,
        history_service=deps.history_service,
        planner_factory=deps.planner_factory,
        emit_run_started_func=deps.emit_agent_os_event_func,
        source_agent_id=source_agent_id,
        profile_name=profile_name,
        model_name=model_name,
        session_id=str(session_id or ""),
        streaming=False,
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
            streaming=False,
            timeline=timeline,
            append_timeline_func=deps.append_timeline_func,
            log_memory_save=True,
        )
        route = execution.route
        temporal = execution.temporal
        web_plan = execution.web_plan
        effective_model = execution.effective_model
        selected_tools = execution.selected_tools

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
            append_timeline_func=deps.append_timeline_func,
        )

        draft = deps.run_chat_func(
            model_name=effective_model,
            profile_name=profile_name,
            user_input=prompt_bundle.prompt + deps.compose_human_style_rules_func(temporal),
            history=bootstrap.history,
            num_ctx=num_ctx,
            task_context=prompt_bundle.task_context,
        )
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        has_generated_files = deps.has_generated_file_attachments_func()
        should_reflect = (route in deps.reflection_routes) or use_reflection
        if should_reflect and answer.strip() and not has_generated_files:
            reflection = deps.run_reflection_loop_func(
                model_name=effective_model,
                profile_name=profile_name,
                user_input=raw_user_input,
                draft_text=answer,
                review_text="Improve.",
                context=prompt_bundle.context_bundle,
            )
            answer = reflection.get("answer") or answer

        attachments = deps.get_and_clear_attachments_func()
        if attachments:
            answer += attachments

        guarded = deps.apply_response_guards_func(
            raw_user_input=raw_user_input,
            text=answer,
            timeline=timeline,
            use_python_exec=use_python_exec,
            use_file_gen=use_file_gen,
            append_timeline_func=deps.append_timeline_func,
            maybe_generate_files_func=deps.maybe_generate_files_func,
            maybe_auto_exec_python_func=deps.maybe_auto_exec_python_func,
        )
        answer = guarded.text

        duration_ms = int((_time.monotonic() - agent_start) * 1000)
        meta = deps.finalize_chat_success_func(
            history_service=deps.history_service,
            run_id=run["run_id"],
            session_id=str(session_id or ""),
            profile_name=profile_name,
            model_name=effective_model,
            route=route,
            user_input=raw_user_input,
            answer_text=answer,
            tools=selected_tools,
            temporal=temporal,
            web_plan=web_plan,
            identity_guard=guarded.identity_guard,
            provenance_guard=guarded.provenance_guard,
            duration_ms=duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=effective_agent_id,
            source_agent_id=source_agent_id,
            selected_tools=selected_tools,
        )
        deps.record_registry_agent_run_func(
            agent_id=agent_id,
            registry_agent=registry_agent,
            run_id=run["run_id"],
            input_summary=raw_user_input,
            output_summary=answer,
            ok=True,
            route=route,
            model_name=effective_model,
            duration_ms=duration_ms,
        )
        return {
            "ok": True,
            "answer": answer,
            "timeline": timeline,
            "tool_results": tool_results,
            "meta": meta,
        }
    except Exception as exc:
        if getattr(exc, "reason", None) is not None and getattr(exc, "details", None) is not None:
            error_step = {"step": "sandbox", "title": "Sandbox", "status": "error", "detail": str(exc)}
            error_meta = {
                "error": str(exc),
                "run_id": run["run_id"],
                "sandbox_reason": exc.reason,
                "sandbox_details": exc.details,
            }
        else:
            error_step = {"step": "error", "title": "Error", "status": "error", "detail": str(exc)}
            error_meta = {"error": str(exc), "run_id": run["run_id"]}
        error_payload = {
            "ok": False,
            "answer": "",
            "timeline": timeline + [error_step],
            "tool_results": tool_results,
            "meta": error_meta,
        }
        duration_ms = int((_time.monotonic() - agent_start) * 1000)
        deps.finalize_chat_failure_func(
            history_service=deps.history_service,
            run_id=run["run_id"],
            profile_name=profile_name,
            model_name=locals().get("effective_model", model_name),
            route=locals().get("route", ""),
            error_text=str(exc),
            duration_ms=duration_ms,
            streaming=False,
            num_ctx=num_ctx,
            agent_id=effective_agent_id,
            source_agent_id=source_agent_id,
            session_id=str(session_id or ""),
            selected_tools=locals().get("selected_tools", []),
            history_payload=error_payload,
        )
        if getattr(exc, "reason", None) is None:
            deps.record_registry_agent_run_func(
                agent_id=agent_id,
                registry_agent=registry_agent,
                run_id=run["run_id"],
                input_summary=raw_user_input if "raw_user_input" in locals() else user_input,
                output_summary=str(exc),
                ok=False,
                route=locals().get("route", ""),
                model_name=locals().get("effective_model", model_name),
                duration_ms=duration_ms,
            )
        return error_payload
