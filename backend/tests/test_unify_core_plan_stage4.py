from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes.chat import router as chat_router  # noqa: E402
from app.application.code_agent import agent_loop  # noqa: E402
from app.application.tool_registry.runtime import seed_builtin_tools  # noqa: E402


def _route_decision(model: str = "stage4-model") -> tuple[str, int, SimpleNamespace]:
    return (
        model,
        32768,
        SimpleNamespace(
            route="code",
            provider="test",
            profile_id="stage4",
            role="code",
            source="test",
            requested_model=model,
            fallback_reason=None,
        ),
    )


def _patch_agent_loop(model: str = "stage4-model"):
    return (
        patch.object(agent_loop, "_resolve_code_route", return_value=_route_decision(model)),
        patch.object(agent_loop, "_record_code_route_metric"),
        patch.object(agent_loop, "record_inference_telemetry"),
        patch.object(agent_loop, "build_mcp_providers", return_value=[]),
        patch(
            "app.application.agent_registry.sandbox.preflight_or_raise",
            return_value={"limit": {"max_execution_seconds": 600}},
        ),
    )


def test_stage4_chat_router_keeps_only_attachment_route() -> None:
    routes = sorted((route.path, sorted(route.methods)) for route in chat_router.routes)

    assert routes == [("/api/chat/attach", ["POST"])]


def test_stage4_plain_chat_uses_code_agent_core_without_tool_calls(tmp_path: Path) -> None:
    def fake_chat(**_kwargs):
        return {"message": {"content": "Привет, я на месте.", "tool_calls": []}}

    patches = _patch_agent_loop()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        events = list(agent_loop.stream_code_agent(
            user_message="привет",
            project_root=tmp_path,
            run_id="stage4-chat",
            auto_remember=False,
            chat_fn=fake_chat,
        ))

    assert not [event for event in events if event.get("type") == "tool_call"]
    assert any(event.get("type") == "final_response" and "Привет" in event.get("text", "") for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["stop_reason"] == "answer"


def test_stage4_chat_can_activate_and_run_light_tool_from_core(tmp_path: Path) -> None:
    seed_builtin_tools()
    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "tool_search",
                        "arguments": {"query": "translator"},
                    }
                }],
            }
        },
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "translator",
                        "arguments": {"text": "привет", "target_lang": "english"},
                    }
                }],
            }
        },
        {"message": {"content": "Translation: hello", "tool_calls": []}},
    ])

    def fake_chat(**_kwargs):
        # A no-tools call is context compaction / wrap-up (small num_ctx=8192
        # forces it once messages grow) — answer it with a summary instead of
        # consuming the scripted tool responses. Only the tool-loop calls (which
        # carry `tools`) advance the script. Mirrors the convention used by the
        # other agent-loop tests; keeps this test about tool activation, not
        # compaction timing.
        if not _kwargs.get("tools"):
            return {"message": {"content": "summary", "tool_calls": []}}
        return next(responses)

    patches = _patch_agent_loop()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
        "app.application.skills_extra.runtime.translate_text",
        return_value={"ok": True, "translated": "hello"},
    ):
        events = list(agent_loop.stream_code_agent(
            user_message="переведи привет",
            project_root=tmp_path,
            run_id="stage4-tool",
            auto_remember=False,
            chat_fn=fake_chat,
        ))

    tool_calls = [event for event in events if event.get("type") == "tool_call"]
    assert [event["tool"] for event in tool_calls] == ["tool_search", "translator"]
    assert tool_calls[-1]["result"] == "hello"
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True


def test_stage4_code_agent_cancel_stops_registered_run(tmp_path: Path) -> None:
    def fake_chat(**_kwargs):
        return {"message": {"content": "should not finish", "tool_calls": []}}

    patches = _patch_agent_loop()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        stream = agent_loop.stream_code_agent(
            user_message="long run",
            project_root=tmp_path,
            run_id="stage4-cancel",
            auto_remember=False,
            chat_fn=fake_chat,
        )
        started = next(stream)
        assert started["type"] == "run_started"
        assert agent_loop.request_cancel("stage4-cancel") is True
        remaining = list(stream)

    assert remaining[-1]["type"] == "done"
    assert remaining[-1]["ok"] is False
    assert remaining[-1]["stop_reason"] == "cancelled"
