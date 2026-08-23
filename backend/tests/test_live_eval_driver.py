from __future__ import annotations

import sys
from pathlib import Path


SMOKES_DIR = Path(__file__).resolve().parent / "smokes"
if str(SMOKES_DIR) not in sys.path:
    sys.path.insert(0, str(SMOKES_DIR))

from driver import summarize_events  # noqa: E402
from routing_eval import evaluate_case, render_markdown, run_suite  # noqa: E402


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
            "arguments": {"cidr": "127.0.0.1/32"},
        },
        {
            "type": "usage",
            "step": 2,
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "tokens_per_second": 21.5,
        },
        {"type": "final_response", "text": "Проверка завершена."},
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
        event_elapsed_s=[0.1, 0.2, 0.8, 1.2, 2.0, 3.5, 4.0, 4.2],
    )

    assert summary["effective_profile"] == "Инфраструктура"
    assert summary["initial_runtime_activation"]["itops"] is True
    assert summary["activated_mcp_server_ids"] == ["context7"]
    assert summary["tool_names"] == [
        "runtime_control",
        "itops_network_inventory",
    ]
    assert summary["runtime_operations"] == ["mcp_start"]
    assert summary["first_action_s"] == 0.8
    assert summary["ttft_s"] == 0.8
    assert summary["prompt_tokens"] == 1200
    assert summary["completion_tokens"] == 80
    assert summary["tokens_per_second"] == 21.5
    assert summary["answer"] == "Проверка завершена."


def test_case_evaluator_checks_profile_tools_runtime_and_activation() -> None:
    spec = {
        "expected_profile": "Инфраструктура",
        "required_tools": ["itops_network_inventory"],
        "required_tool_prefixes": ["context7__"],
        "forbidden_tools": ["run_bash"],
        "required_runtime_operations": ["mcp_start"],
        "required_mcp_servers": ["context7"],
        "required_answer_contains": ["Проверка завершена"],
        "expected_initial_activation": {"itops": True},
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
        "activated_mcp_server_ids": ["context7"],
        "initial_runtime_activation": {"itops": True, "capability_groups": []},
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
        },
        "code": {
            "stop_reason": "answer",
            "effective_profile": "Баланс",
            "tool_names": [],
            "tool_calls": 0,
            "duration_s": 5.0,
            "ttft_s": 1.5,
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
    }
    assert report["results"]["infra"]["status"] == "PASS"
    assert report["results"]["code"]["status"] == "FAIL"
    assert "missing tool: read_file" in report["results"]["code"]["failures"]

    markdown = render_markdown(report)
    assert "# Elira agent routing eval — eval-fixed" in markdown
    assert "| infra | PASS | Инфраструктура |" in markdown
    assert "missing tool: read_file" in markdown
