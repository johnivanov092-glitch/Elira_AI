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
