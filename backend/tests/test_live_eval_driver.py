from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest


SMOKES_DIR = Path(__file__).resolve().parent / "smokes"
if str(SMOKES_DIR) not in sys.path:
    sys.path.insert(0, str(SMOKES_DIR))
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from driver import run_smoke, summarize_events  # noqa: E402


def test_source_reporting_excludes_failed_pages_and_does_not_certify_claims():
    summary = summarize_events([
        {"type": "tool_call", "tool": "web_fetch", "ok": False, "result": "ERROR https://failed.test/"},
        {"type": "tool_call", "tool": "web_fetch", "ok": True, "result": "batch", "sources": [
            {"status": "failed", "url": "https://failed.test/"},
            {"status": "excerpt", "url": "https://read.test/"},
        ]},
        {"type": "final_response", "text": "An unsupported claim", "source_status": "matched", "citations": [
            {"source_id": "one", "status": "matched", "claim_support": "not_assessed"},
        ]},
    ], run_id="sources", duration_s=1)
    assert summary["tool_source_urls"] == {"web_fetch": ["https://read.test/"]}
    assert summary["source_status"] == "matched"
    assert summary["claim_support"] == "not_assessed"
from routing_eval import (  # noqa: E402
    DEFAULT_CASES_PATH,
    _load_cases,
    _prepare_case_workspace,
    evaluate_case,
    render_markdown,
    run_suite,
)


def test_live_driver_resolves_scripted_workflow_input_through_public_api(
    tmp_path: Path,
) -> None:
    resolved = threading.Event()
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, payload: dict) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/api/agent-os/workflow-runs":
                self._json({
                    "runs": [{
                        "run_id": "wf-scripted",
                        "context": {"code_agent_run_id": "eval-scripted"},
                    }],
                    "total": 1,
                })
                return
            if path == "/api/agent-os/workflow-runs/wf-scripted/requests":
                requests = [] if resolved.is_set() else [{
                    "request_id": "req-scripted",
                    "run_id": "wf-scripted",
                    "kind": "input",
                    "status": "pending",
                    "message": "Укажите текст факта",
                    "schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }]
                self._json({"requests": requests, "total": len(requests)})
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/code-agent/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"type":"run_started","profile_name":"Personal"}\n\n')
                self.wfile.flush()
                assert resolved.wait(5), "scripted Workflow response was not sent"
                self.wfile.write(
                    b'data: {"type":"final_response","text":"done"}\n\n'
                    b'data: {"type":"done","stop_reason":"answer"}\n\n'
                )
                self.wfile.flush()
                return
            if path == "/api/agent-os/workflow-requests/req-scripted/resolve":
                received.append(payload)
                resolved.set()
                self._json({"request": {"status": "resolved"}, "run": {"status": "running"}})
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        summary = run_smoke(
            backend=f"http://127.0.0.1:{server.server_port}",
            task_text="Запомни факт",
            project_root=str(tmp_path),
            run_id="eval-scripted",
            events_path=tmp_path / "events.jsonl",
            timeout_s=10,
            workflow_responses=[{
                "kind": "input",
                "message_contains": "текст факта",
                "values": {"query": "SCRIPTED_CANARY"},
            }],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert received == [{
        "action": "accept",
        "values": {"query": "SCRIPTED_CANARY"},
    }]
    assert summary["stop_reason"] == "answer"
    assert summary["workflow_requests_resolved"] == [{
        "request_id": "req-scripted",
        "kind": "input",
        "action": "accept",
    }]
    assert summary["workflow_response_errors"] == []
    assert summary["workflow_responses_unused"] == 0


def test_live_driver_rejects_plaintext_secret_and_fake_elevation(tmp_path: Path) -> None:
    common = {
        "backend": "http://127.0.0.1:1",
        "task_text": "request",
        "project_root": str(tmp_path),
        "run_id": "invalid-script",
        "events_path": tmp_path / "events.jsonl",
    }
    with pytest.raises(ValueError, match="secret_ref"):
        run_smoke(
            **common,
            workflow_responses=[{
                "kind": "secret",
                "values": {"token": "plaintext-is-forbidden"},
            }],
        )
    with pytest.raises(ValueError, match="cannot fake elevation"):
        run_smoke(
            **common,
            workflow_responses=[{
                "kind": "elevation",
                "action": "accept",
                "values": {"elevated": True},
            }],
        )


def test_live_driver_stops_the_run_when_the_selected_tool_starts(tmp_path: Path) -> None:
    cancelled = threading.Event()
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/code-agent/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'data: {"type":"run_started","profile_name":"Engineering"}\n\n'
                    b'data: {"type":"tool_started","tool":"run_bash","arguments":{}}\n\n'
                )
                self.wfile.flush()
                assert cancelled.wait(5), "Workflow Stop was not sent"
                self.wfile.write(
                    b'data: {"type":"done","stop_reason":"cancelled","error":"Cancelled by user"}\n\n'
                )
                self.wfile.flush()
                return
            if path == "/api/code-agent/cancel":
                received.append(payload)
                cancelled.set()
                encoded = b'{"ok":true,"found":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        summary = run_smoke(
            backend=f"http://127.0.0.1:{server.server_port}",
            task_text="Run a long command",
            project_root=str(tmp_path),
            run_id="eval-stop",
            events_path=tmp_path / "stop-events.jsonl",
            timeout_s=10,
            cancel_on_tool="run_bash",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert received == [{"run_id": "eval-stop"}]
    assert summary["stop_reason"] == "cancelled"
    assert summary["workflow_stop_requested"] is True


def test_live_driver_resumes_the_same_run_through_public_api(tmp_path: Path) -> None:
    cancelled = threading.Event()
    resumed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _sse(self, chunks: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(chunks)
            self.wfile.flush()

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/code-agent/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'data: {"type":"run_started","profile_name":"Engineering"}\n\n'
                    b'data: {"type":"tool_started","tool":"run_bash","arguments":{}}\n\n'
                )
                self.wfile.flush()
                assert cancelled.wait(5)
                self.wfile.write(
                    b'data: {"type":"done","stop_reason":"cancelled","resumable":true}\n\n'
                )
                self.wfile.flush()
                return
            if path == "/api/code-agent/cancel":
                assert payload == {"run_id": "eval-resume"}
                cancelled.set()
                encoded = b'{"ok":true,"found":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if path == "/api/code-agent/runs/eval-resume/resume":
                resumed.set()
                self._sse(
                    b'data: {"type":"run_started","profile_name":"Engineering"}\n\n'
                    b'data: {"type":"final_response","text":"RESUME_COMPLETE"}\n\n'
                    b'data: {"type":"done","stop_reason":"answer"}\n\n'
                )
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        summary = run_smoke(
            backend=f"http://127.0.0.1:{server.server_port}",
            task_text="Stop and resume",
            project_root=str(tmp_path),
            run_id="eval-resume",
            events_path=tmp_path / "resume-events.jsonl",
            timeout_s=10,
            cancel_on_tool="run_bash",
            resume_after_stop=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert resumed.is_set()
    assert summary["pre_resume_stop_reason"] == "cancelled"
    assert summary["workflow_resume_attempted"] is True
    assert summary["workflow_resume_error"] == ""
    assert summary["stop_reason"] == "answer"
    assert summary["answer"] == "RESUME_COMPLETE"


def test_case_evaluator_checks_scripted_workflow_responses() -> None:
    spec = {
        "required_workflow_responses": [{"kind": "input", "action": "accept"}],
    }
    summary = {
        "stop_reason": "answer",
        "workflow_requests_resolved": [{
            "request_id": "req-1",
            "kind": "input",
            "action": "accept",
        }],
        "workflow_response_errors": [],
        "workflow_responses_unused": 0,
    }

    assert evaluate_case(spec, summary) == []

    summary["workflow_response_errors"] = ["request failed"]
    summary["workflow_responses_unused"] = 1
    failures = evaluate_case(spec, summary)
    assert "Workflow scripted response error: request failed" in failures
    assert "unused Workflow scripted responses: 1" in failures


def test_case_evaluator_can_expect_an_explicit_workflow_stop() -> None:
    spec = {"expected_stop_reason": "cancelled"}
    summary = {"stop_reason": "cancelled", "error": "Cancelled by user"}

    assert evaluate_case(spec, summary) == []


def test_default_suite_skips_opt_in_cases_but_explicit_selection_runs_them() -> None:
    default_cases = _load_cases(DEFAULT_CASES_PATH, set())
    selected = _load_cases(DEFAULT_CASES_PATH, {"project_corpus_workflow"})

    assert "project_corpus_workflow" not in default_cases
    assert list(selected) == ["project_corpus_workflow"]


def test_case_loader_filters_named_harness_suites() -> None:
    core_cases = _load_cases(DEFAULT_CASES_PATH, set(), suite="core")
    integration_cases = _load_cases(
        DEFAULT_CASES_PATH,
        set(),
        include_opt_in=True,
        suite="integration",
    )

    assert core_cases
    assert all(case.get("suite") == "core" for case in core_cases.values())
    assert integration_cases
    assert all(case.get("suite") == "integration" for case in integration_cases.values())


def test_no_project_case_uses_an_external_absolute_workspace(tmp_path: Path) -> None:
    task, project_root = _prepare_case_workspace(
        tmp_path,
        "absolute_path_no_project",
        {
            "project_mode": "none",
            "task": "Прочитай {ABSOLUTE_TARGET}\\README.md",
            "files": {"README.md": "ABSOLUTE_PATH_CANARY"},
        },
    )

    assert project_root == ""
    assert "{ABSOLUTE_TARGET}" not in task
    assert str((tmp_path / "external" / "absolute_path_no_project").resolve()) in task
    assert (tmp_path / "external" / "absolute_path_no_project" / "README.md").is_file()


def test_case_workspace_expands_the_pinned_python_lsp_executable(tmp_path: Path) -> None:
    task, _project_root = _prepare_case_workspace(
        tmp_path,
        "lsp_python",
        {"task": "Запусти {PYRIGHT_LANGSERVER}"},
    )

    assert "{PYRIGHT_LANGSERVER}" not in task
    assert "backend" in task
    assert "pyright-langserver" in task


def test_agent_sse_forwards_structured_background_job_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.application.agent_kernel.executor import ToolExecutionResult
    from app.application.code_agent import agent_loop

    responses = iter([
        {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "run_server",
                        "arguments": {
                            "action": "start",
                            "kind": "job",
                            "command": "python durable_probe.py",
                        },
                    },
                }],
            },
        },
        {"message": {"content": "Готово.", "tool_calls": []}},
    ])

    monkeypatch.setattr(
        agent_loop,
        "_kernel_exec",
        lambda *_args, **_kwargs: ToolExecutionResult(
            status="ok",
            output={
                "ok": True,
                "text": "Job started",
                "action": "start",
                "kind": "job",
                "status": "running",
                "job_id": "job-1",
                "pid": 42,
                "log_path": str(tmp_path / "job.log"),
                "recovered": False,
            },
        ),
    )
    events = list(agent_loop.stream_code_agent(
        user_message="Запусти фоновую задачу",
        project_root=tmp_path,
        chat_fn=lambda **_kwargs: next(responses),
        permission_mode="bypass",
        auto_remember=False,
    ))
    event = next(item for item in events if item.get("type") == "tool_call")

    assert event["action"] == "start"
    assert event["kind"] == "job"
    assert event["status"] == "running"
    assert event["job_id"] == "job-1"
    assert event["pid"] == 42
    assert event["log_path"].endswith("job.log")
    assert event["recovered"] is False


def test_sse_summary_reports_profile_tools_mcp_and_latency() -> None:
    events = [
        {
            "type": "run_started",
            "profile_name": "Инфраструктура",
            "runtime_activation": {
                "itops": True,
                "mcp_server_ids": [],
                "capability_groups": [],
            },
        },
        {"type": "step_started", "step": 1},
        {"type": "reasoning_delta", "step": 1, "text": "Проверяю"},
        {
            "type": "tool_call",
            "step": 1,
            "tool": "runtime_control",
            "arguments": {"operation": "mcp_start", "server_id": "context7"},
            "ok": True,
            "runtime_activation": {
                "itops": True,
                "mcp_server_ids": ["context7"],
                "capability_groups": [],
            },
        },
        {
            "type": "tool_call",
            "step": 2,
            "tool": "itops_network_inventory",
            "arguments": {"cidr": "127.0.0.1/32", "ports": [8000, 65534]},
            "ok": True,
            "result": (
                "Network inventory 127.0.0.1/32: status=complete; "
                "open_endpoints=127.0.0.1:8000"
            ),
        },
        {
            "type": "tool_call",
            "step": 2,
            "tool": "web_search",
            "arguments": {"query": "official source"},
            "ok": True,
            "result": "Source: https://example.test/source",
        },
        {
            "type": "tool_call",
            "step": 2,
            "tool": "runtime_control",
            "arguments": {"operation": "mcp_stop", "server_id": "context7"},
            "ok": True,
            "runtime_activation": {
                "itops": True,
                "mcp_server_ids": [],
                "capability_groups": [],
            },
        },
        {
            "type": "usage",
            "step": 2,
            "prompt_tokens": 1200,
            "cached_prompt_tokens": 900,
            "cache_hit_ratio": 0.75,
            "completion_tokens": 80,
            "prompt_tokens_per_second": 350.5,
            "tokens_per_second": 21.5,
            "ttft_ms": 420,
        },
        {
            "type": "final_response",
            "text": (
                "Порт 8000 открыт, порт 65534 не открыт. "
                "Источник: https://example.test/source"
            ),
        },
        {
            "type": "done",
            "stop_reason": "answer",
            "completion_status": "confirmed",
            "criteria": [],
        },
    ]

    summary = summarize_events(
        events,
        run_id="eval-1",
        duration_s=4.2,
        event_elapsed_s=[
            0.1, 0.2, 0.8, 1.2, 2.0, 2.4, 2.8, 3.5, 4.0, 4.2,
        ],
    )

    assert summary["effective_profile"] == "Инфраструктура"
    assert summary["initial_runtime_activation"]["itops"] is True
    assert summary["activated_mcp_server_ids"] == ["context7"]
    assert summary["tool_names"] == [
        "runtime_control",
        "itops_network_inventory",
        "web_search",
        "runtime_control",
    ]
    assert summary["runtime_operations"] == ["mcp_start", "mcp_stop"]
    assert summary["runtime_calls"] == [
        {"operation": "mcp_start", "server_id": "context7", "ok": True},
        {"operation": "mcp_stop", "server_id": "context7", "ok": True},
    ]
    assert summary["final_runtime_activation"]["mcp_server_ids"] == []
    assert summary["tool_source_urls"]["web_search"] == [
        "https://example.test/source",
    ]
    assert summary["answer_urls"] == ["https://example.test/source"]
    assert summary["network_inventories"] == [{
        "cidr": "127.0.0.1/32",
        "requested_ports": [8000, 65534],
        "open_ports": [8000],
        "ok": True,
    }]
    assert summary["first_action_s"] == 0.8
    assert summary["ttft_s"] == 0.8
    assert summary["prompt_tokens"] == 1200
    assert summary["cached_prompt_tokens"] == 900
    assert summary["cache_hit_ratio"] == 0.75
    assert summary["completion_tokens"] == 80
    assert summary["prompt_tokens_per_second"] == 350.5
    assert summary["tokens_per_second"] == 21.5
    assert summary["model_ttft_ms"] == 420
    assert "Порт 8000 открыт" in summary["answer"]


def test_sse_summary_replays_redacted_journal_metrics_and_flags_false_success() -> None:
    events = [
        {"type": "run_started", "profile_name": "Инженерный"},
        {
            "type": "tool_call",
            "tool": "run_server",
            "arguments": {"action": "stop", "pid": 123},
            "ok": True,
            "result": "ERROR: no tracked server with pid=123.",
        },
        {
            "type": "usage",
            "prompt_tokens": 1000,
            "cached_prompt_tokens": "[REDACTED]",
            "cache_hit_ratio": 0.8,
            "prompt_tokens_per_second": "[REDACTED]",
            "tokens_per_second": 30.0,
            "ttft_ms": 1200,
            "context": {
                "current_tokens": 900,
                "breakdown": {"tools": 700},
            },
        },
        {"type": "done", "stop_reason": "answer", "resumable": False},
    ]

    summary = summarize_events(events, run_id="journal-run", duration_s=2.0)

    assert summary["cached_prompt_tokens"] == 0
    assert summary["cached_prompt_tokens_available"] is False
    assert summary["cache_hit_ratio"] == 0.8
    assert summary["prompt_tokens_per_second"] == 0.0
    assert summary["error_prefixed_successes"] == 1
    assert summary["failed_tool_calls"] == 0
    assert summary["final_tool_context_tokens"] == 700


def test_live_eval_can_require_background_job_start_then_completed_logs() -> None:
    events = [
        {"type": "run_started", "profile_name": "Инженерный"},
        {
            "type": "tool_call",
            "tool": "run_server",
            "arguments": {"action": "start", "kind": "job", "command": "worker"},
            "ok": True,
            "status": "running",
        },
        {
            "type": "tool_call",
            "tool": "run_server",
            "arguments": {"action": "logs", "pid": 42},
            "ok": True,
            "status": "completed",
            "exit_code": 0,
        },
        {"type": "final_response", "text": "DURABLE_LIVE_DONE exit=0"},
        {"type": "done", "stop_reason": "answer", "criteria": []},
    ]
    summary = summarize_events(events, run_id="job-live", duration_s=2.0)
    assert summary["tool_trace"] == [
        {
            "tool": "run_server",
            "action": "start",
            "kind": "job",
            "status": "running",
            "ok": True,
        },
        {
            "tool": "run_server",
            "action": "logs",
            "status": "completed",
            "ok": True,
        },
    ]
    spec = {
        "expected_profile": "Инженерный",
        "required_tools": ["run_server"],
        "required_tool_sequence": [
            {"tool": "run_server", "action": "start", "kind": "job", "status": "running"},
            {"tool": "run_server", "action": "logs", "status": "completed"},
        ],
        "required_answer_contains": ["DURABLE_LIVE_DONE", "exit=0"],
        "max_tool_calls": 3,
    }

    assert evaluate_case(spec, summary) == []


def test_case_evaluator_checks_profile_tools_runtime_and_activation() -> None:
    spec = {
        "expected_profile": "Инфраструктура",
        "required_tools": ["itops_network_inventory"],
        "required_tool_prefixes": ["context7__"],
        "forbidden_tools": ["run_bash"],
        "required_runtime_operations": ["mcp_start"],
        "required_tool_sequence": [
            {"tool": "runtime_control", "operation": "mcp_start", "server_id": "context7"},
            {"tool_prefix": "context7__"},
            {"tool": "runtime_control", "operation": "mcp_stop", "server_id": "context7"},
        ],
        "required_mcp_servers": ["context7"],
        "required_answer_contains": ["Проверка завершена"],
        "expected_initial_activation": {"itops": True},
        "expected_final_activation": {"mcp_server_ids": []},
        "max_tool_calls": 4,
    }
    summary = {
        "stop_reason": "answer",
        "effective_profile": "Инфраструктура",
        "tool_names": [
            "runtime_control",
            "context7__query-docs",
            "itops_network_inventory",
        ],
        "runtime_operations": ["mcp_start"],
        "runtime_calls": [
            {"operation": "mcp_start", "server_id": "context7", "ok": True},
            {"operation": "mcp_stop", "server_id": "context7", "ok": True},
        ],
        "tool_trace": [
            {
                "tool": "runtime_control",
                "operation": "mcp_start",
                "server_id": "context7",
                "ok": True,
            },
            {"tool": "context7__query-docs", "ok": True},
            {
                "tool": "runtime_control",
                "operation": "mcp_stop",
                "server_id": "context7",
                "ok": True,
            },
        ],
        "activated_mcp_server_ids": ["context7"],
        "initial_runtime_activation": {"itops": True, "capability_groups": []},
        "final_runtime_activation": {"mcp_server_ids": []},
        "tool_calls": 3,
        "answer": "Проверка завершена: открыт 127.0.0.1:8000.",
    }

    assert evaluate_case(spec, summary) == []

    summary["effective_profile"] = "Баланс"
    summary["tool_names"] = ["run_bash"]
    failures = evaluate_case(spec, summary)

    assert "profile=Баланс; expected=Инфраструктура" in failures
    assert "missing tool: itops_network_inventory" in failures
    assert "missing tool prefix: context7__" in failures
    assert "forbidden tool used: run_bash" in failures

    summary["answer"] = "Нет итогового результата."
    failures = evaluate_case(spec, summary)
    assert "answer is missing: Проверка завершена" in failures


def test_case_evaluator_requires_successful_ordered_mcp_shutdown() -> None:
    spec = {
        "required_tool_sequence": [
            {"tool": "runtime_control", "operation": "mcp_start", "server_id": "context7"},
            {"tool_prefix": "context7__"},
            {"tool": "runtime_control", "operation": "mcp_stop", "server_id": "context7"},
        ],
        "expected_final_activation": {"mcp_server_ids": []},
    }
    summary = {
        "stop_reason": "answer",
        "tool_names": ["runtime_control", "context7__query-docs", "runtime_control"],
        "tool_trace": [
            {
                "tool": "runtime_control",
                "operation": "mcp_start",
                "server_id": "context7",
                "ok": True,
            },
            {"tool": "context7__query-docs", "ok": True},
            {
                "tool": "runtime_control",
                "operation": "mcp_stop",
                "server_id": "context7",
                "ok": False,
            },
        ],
        "final_runtime_activation": {"mcp_server_ids": ["context7"]},
    }

    failures = evaluate_case(spec, summary)

    assert any("tool sequence" in failure for failure in failures)
    assert "final activation mcp_server_ids=['context7']; expected=[]" in failures


def test_case_evaluator_forbids_any_mcp_activation_in_negative_case() -> None:
    spec = {
        "forbidden_runtime_operation_prefixes": ["mcp_"],
        "forbid_mcp_activation": True,
    }
    summary = {
        "stop_reason": "answer",
        "tool_names": ["runtime_control"],
        "runtime_operations": ["mcp_restart"],
        "activated_mcp_server_ids": ["context7"],
    }

    failures = evaluate_case(spec, summary)

    assert "forbidden runtime operation used: mcp_restart" in failures
    assert "MCP activation is forbidden: context7" in failures


def test_case_evaluator_grounds_network_states_and_citations_to_tool_results() -> None:
    spec = {
        "require_network_answer_grounding": True,
        "answer_source_tools": ["web_search"],
    }
    summary = {
        "stop_reason": "answer",
        "answer": (
            "Порт 8000 закрыт, а 65534 открыт. "
            "Источник: https://unrelated.test/page"
        ),
        "answer_urls": ["https://unrelated.test/page"],
        "tool_source_urls": {"web_search": ["https://official.test/page"]},
        "network_inventories": [{
            "cidr": "127.0.0.1/32",
            "requested_ports": [8000, 65534],
            "open_ports": [8000],
            "ok": True,
        }],
    }

    failures = evaluate_case(spec, summary)

    assert "answer does not cite a URL returned by: web_search" in failures
    assert "answer does not report port 8000 as open" in failures
    assert "answer does not report port 65534 as not open" in failures


def test_case_evaluator_rejects_an_action_claim_without_successful_evidence() -> None:
    spec = {
        "grounded_action_claims": [{
            "answer_contains_any": ["отправлено", "сообщение отправлено"],
            "evidence": {"tool": "runtime_control", "operation": "telegram_send"},
        }],
    }
    summary = {
        "stop_reason": "answer",
        "answer": "Сообщение отправлено.",
        "tool_trace": [{
            "tool": "runtime_control",
            "operation": "telegram_send",
            "ok": False,
        }],
    }

    failures = evaluate_case(spec, summary)

    assert failures == [
        "action claim is not grounded by a successful tool result: telegram_send"
    ]

    summary["tool_trace"][0]["ok"] = True
    assert evaluate_case(spec, summary) == []


def test_suite_report_keeps_each_failure_and_aggregates_metrics() -> None:
    cases = {
        "infra": {
            "expected_profile": "Инфраструктура",
            "required_tools": ["itops_network_inventory"],
        },
        "code": {
            "expected_profile": "Инженерный",
            "required_tools": ["read_file"],
        },
    }
    summaries = {
        "infra": {
            "stop_reason": "answer",
            "effective_profile": "Инфраструктура",
            "tool_names": ["itops_network_inventory"],
            "tool_calls": 1,
            "duration_s": 3.0,
            "ttft_s": 0.5,
            "model_ttft_ms": 300,
            "cache_hit_ratio": 0.8,
            "prompt_tokens_per_second": 400.0,
            "tokens_per_second": 40.0,
        },
        "code": {
            "stop_reason": "answer",
            "effective_profile": "Баланс",
            "tool_names": [],
            "tool_calls": 0,
            "duration_s": 5.0,
            "ttft_s": 1.5,
            "model_ttft_ms": 500,
            "cache_hit_ratio": 0.2,
            "prompt_tokens_per_second": 300.0,
            "tokens_per_second": 30.0,
        },
    }

    report = run_suite(
        cases,
        suite_id="eval-fixed",
        execute_case=lambda name, _spec: summaries[name],
    )

    assert report["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "profile_accuracy": 0.5,
        "average_duration_s": 4.0,
        "average_ttft_s": 1.0,
        "average_model_ttft_ms": 400.0,
        "average_cache_hit_ratio": 0.5,
        "average_prompt_tokens_per_second": 350.0,
        "average_tokens_per_second": 35.0,
    }
    assert report["results"]["infra"]["status"] == "PASS"
    assert report["results"]["code"]["status"] == "FAIL"
    assert "missing tool: read_file" in report["results"]["code"]["failures"]

    markdown = render_markdown(report)
    assert "# Elira agent routing eval — eval-fixed" in markdown
    assert "| infra | PASS | Инфраструктура |" in markdown
    assert "cache: 50.0%" in markdown
    assert "missing tool: read_file" in markdown
