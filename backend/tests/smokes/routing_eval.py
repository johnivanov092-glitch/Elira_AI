# -*- coding: utf-8 -*-
"""Live profile/tool/MCP evals through the public code-agent SSE endpoint."""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driver import run_smoke


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
DEFAULT_CASES_PATH = HERE / "routing_cases.json"
_NON_OPEN_TERMS = (
    "не открыт", "закрыт", "closed", "not open", "timeout", "refused",
    "не ответ", "недоступ",
)


def _trace_item_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    tool_name = str(actual.get("tool") or "")
    prefix = str(expected.get("tool_prefix") or "")
    if prefix and not tool_name.startswith(prefix):
        return False
    for key in ("tool", "operation", "server_id"):
        value = expected.get(key)
        if value is not None and str(actual.get(key) or "") != str(value):
            return False
    return actual.get("ok") is True


def _port_states(answer: str, port: int) -> set[str]:
    states: set[str] = set()
    for clause in re.split(r"[\n,;]|(?<=[.!?])\s+", answer.casefold()):
        if re.search(rf"(?<!\d){int(port)}(?!\d)", clause) is None:
            continue
        if any(term in clause for term in _NON_OPEN_TERMS):
            states.add("not_open")
        elif re.search(r"\bopen\b|открыт\w*", clause):
            states.add("open")
    return states


def evaluate_case(spec: dict[str, Any], summary: dict[str, Any]) -> list[str]:
    """Return contract failures for one live routing case."""
    failures: list[str] = []
    if summary.get("stop_reason") != "answer":
        failures.append(
            f"stop_reason={summary.get('stop_reason')}; error={str(summary.get('error') or '')[:160]}"
        )

    expected_profile = str(spec.get("expected_profile") or "")
    actual_profile = str(summary.get("effective_profile") or "")
    if expected_profile and actual_profile != expected_profile:
        failures.append(f"profile={actual_profile}; expected={expected_profile}")

    tool_names = {str(name) for name in summary.get("tool_names") or []}
    for tool_name in spec.get("required_tools") or []:
        if str(tool_name) not in tool_names:
            failures.append(f"missing tool: {tool_name}")
    for prefix in spec.get("required_tool_prefixes") or []:
        if not any(tool_name.startswith(str(prefix)) for tool_name in tool_names):
            failures.append(f"missing tool prefix: {prefix}")
    for tool_name in spec.get("forbidden_tools") or []:
        if str(tool_name) in tool_names:
            failures.append(f"forbidden tool used: {tool_name}")

    runtime_calls = summary.get("runtime_calls")
    if isinstance(runtime_calls, list):
        operations = {
            str(call.get("operation") or "")
            for call in runtime_calls
            if isinstance(call, dict) and call.get("ok") is True
        }
    else:  # backwards-compatible evaluation of reports written by older drivers
        operations = {str(name) for name in summary.get("runtime_operations") or []}
    all_operations = {str(name) for name in summary.get("runtime_operations") or []}
    for operation in spec.get("required_runtime_operations") or []:
        if str(operation) not in operations:
            failures.append(f"missing runtime operation: {operation}")
    for operation in spec.get("forbidden_runtime_operations") or []:
        if str(operation) in all_operations:
            failures.append(f"forbidden runtime operation used: {operation}")
    for prefix in spec.get("forbidden_runtime_operation_prefixes") or []:
        for operation in sorted(all_operations):
            if operation.startswith(str(prefix)):
                failures.append(f"forbidden runtime operation used: {operation}")

    tool_trace = summary.get("tool_trace") or []
    cursor = 0
    for position, expected in enumerate(spec.get("required_tool_sequence") or [], 1):
        found = next(
            (
                index
                for index in range(cursor, len(tool_trace))
                if isinstance(tool_trace[index], dict)
                and _trace_item_matches(dict(expected), tool_trace[index])
            ),
            None,
        )
        if found is None:
            failures.append(f"missing successful tool sequence item {position}: {expected}")
            break
        cursor = found + 1

    active_mcp = {str(name) for name in summary.get("activated_mcp_server_ids") or []}
    for server_id in spec.get("required_mcp_servers") or []:
        if str(server_id) not in active_mcp:
            failures.append(f"MCP server was not activated: {server_id}")
    if spec.get("forbid_mcp_activation") and active_mcp:
        failures.append(f"MCP activation is forbidden: {', '.join(sorted(active_mcp))}")

    initial_activation = summary.get("initial_runtime_activation") or {}
    for key, expected in (spec.get("expected_initial_activation") or {}).items():
        actual = initial_activation.get(key)
        if actual != expected:
            failures.append(f"initial activation {key}={actual!r}; expected={expected!r}")
    final_activation = summary.get("final_runtime_activation") or {}
    for key, expected in (spec.get("expected_final_activation") or {}).items():
        actual = final_activation.get(key)
        if actual != expected:
            failures.append(f"final activation {key}={actual!r}; expected={expected!r}")

    answer = str(summary.get("answer") or "")
    folded_answer = answer.casefold()
    for fragment in spec.get("required_answer_contains") or []:
        if str(fragment).casefold() not in folded_answer:
            failures.append(f"answer is missing: {fragment}")
    for fragment in spec.get("forbidden_answer_contains") or []:
        if str(fragment).casefold() in folded_answer:
            failures.append(f"answer contains forbidden text: {fragment}")

    source_tools = [str(name) for name in spec.get("answer_source_tools") or []]
    if source_tools:
        tool_source_urls = summary.get("tool_source_urls") or {}
        observed_urls = {
            str(url)
            for tool_name in source_tools
            for url in tool_source_urls.get(tool_name) or []
        }
        answer_urls = {str(url) for url in summary.get("answer_urls") or []}
        if not observed_urls.intersection(answer_urls):
            failures.append(
                f"answer does not cite a URL returned by: {', '.join(source_tools)}"
            )

    if spec.get("require_network_answer_grounding"):
        inventories = [
            item for item in summary.get("network_inventories") or []
            if isinstance(item, dict) and item.get("ok") is True
        ]
        if not inventories:
            failures.append("no successful network inventory to ground the answer")
        else:
            inventory = inventories[-1]
            open_ports = {int(port) for port in inventory.get("open_ports") or []}
            for port_value in inventory.get("requested_ports") or []:
                port = int(port_value)
                states = _port_states(answer, port)
                expected_state = "open" if port in open_ports else "not_open"
                opposite_state = "not_open" if expected_state == "open" else "open"
                if expected_state not in states or opposite_state in states:
                    label = "open" if expected_state == "open" else "not open"
                    failures.append(f"answer does not report port {port} as {label}")

    max_tool_calls = spec.get("max_tool_calls")
    if max_tool_calls is not None and int(summary.get("tool_calls") or 0) > int(max_tool_calls):
        failures.append(
            f"tool_calls={summary.get('tool_calls')} > max_tool_calls={max_tool_calls}"
        )
    return failures


def run_suite(
    cases: dict[str, dict[str, Any]],
    *,
    suite_id: str,
    execute_case: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Execute cases and aggregate the observable routing contract."""
    results: dict[str, dict[str, Any]] = {}
    profile_matches = 0
    durations: list[float] = []
    ttfts: list[float] = []
    model_ttfts: list[float] = []
    cache_hit_ratios: list[float] = []
    prompt_token_rates: list[float] = []
    token_rates: list[float] = []
    for name, spec in cases.items():
        summary = execute_case(name, spec)
        failures = evaluate_case(spec, summary)
        expected_profile = str(spec.get("expected_profile") or "")
        if expected_profile and summary.get("effective_profile") == expected_profile:
            profile_matches += 1
        if summary.get("duration_s") is not None:
            durations.append(float(summary["duration_s"]))
        if summary.get("ttft_s") is not None:
            ttfts.append(float(summary["ttft_s"]))
        if float(summary.get("model_ttft_ms") or 0.0) > 0:
            model_ttfts.append(float(summary["model_ttft_ms"]))
        if "cache_hit_ratio" in summary:
            cache_hit_ratios.append(float(summary.get("cache_hit_ratio") or 0.0))
        if float(summary.get("prompt_tokens_per_second") or 0.0) > 0:
            prompt_token_rates.append(float(summary["prompt_tokens_per_second"]))
        if float(summary.get("tokens_per_second") or 0.0) > 0:
            token_rates.append(float(summary["tokens_per_second"]))
        results[name] = {
            "status": "FAIL" if failures else "PASS",
            "failures": failures,
            "summary": summary,
        }

    total = len(cases)
    passed = sum(1 for result in results.values() if result["status"] == "PASS")
    return {
        "suite_id": suite_id,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "profile_accuracy": round(profile_matches / total, 3) if total else 0.0,
            "average_duration_s": round(sum(durations) / len(durations), 3) if durations else 0.0,
            "average_ttft_s": round(sum(ttfts) / len(ttfts), 3) if ttfts else 0.0,
            "average_model_ttft_ms": round(
                sum(model_ttfts) / len(model_ttfts), 1
            ) if model_ttfts else 0.0,
            "average_cache_hit_ratio": round(
                sum(cache_hit_ratios) / len(cache_hit_ratios), 4
            ) if cache_hit_ratios else 0.0,
            "average_prompt_tokens_per_second": round(
                sum(prompt_token_rates) / len(prompt_token_rates), 1
            ) if prompt_token_rates else 0.0,
            "average_tokens_per_second": round(
                sum(token_rates) / len(token_rates), 1
            ) if token_rates else 0.0,
        },
        "results": results,
    }


def _markdown_cell(value: object) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact human-readable companion to results.json."""
    aggregate = report.get("summary") or {}
    lines = [
        f"# Elira agent routing eval — {report.get('suite_id', '')}",
        "",
        (
            f"PASS: {aggregate.get('passed', 0)}/{aggregate.get('total', 0)} · "
            f"profile accuracy: {float(aggregate.get('profile_accuracy') or 0) * 100:.1f}% · "
            f"cache: {float(aggregate.get('average_cache_hit_ratio') or 0) * 100:.1f}% · "
            f"prompt/output: {aggregate.get('average_prompt_tokens_per_second', 0)}/"
            f"{aggregate.get('average_tokens_per_second', 0)} tok/s · "
            f"model TTFT: {aggregate.get('average_model_ttft_ms', 0)}ms · "
            f"avg TTFT: {aggregate.get('average_ttft_s', 0)}s · "
            f"avg duration: {aggregate.get('average_duration_s', 0)}s"
        ),
        "",
        "| Case | Status | Profile | Tools | Cache | Prompt t/s | Output t/s | Model TTFT | Workflow TTFT | Duration | Failures |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, result in (report.get("results") or {}).items():
        summary = result.get("summary") or {}
        failures = "; ".join(result.get("failures") or []) or "—"
        tools = ", ".join(summary.get("tool_names") or []) or "—"
        lines.append(
            "| "
            + " | ".join(_markdown_cell(value) for value in (
                name,
                result.get("status"),
                summary.get("effective_profile"),
                tools,
                f"{float(summary.get('cache_hit_ratio') or 0) * 100:.1f}%",
                summary.get("prompt_tokens_per_second"),
                summary.get("tokens_per_second"),
                summary.get("model_ttft_ms"),
                summary.get("ttft_s"),
                summary.get("duration_s"),
                failures,
            ))
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_cases(path: Path, only: set[str]) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("routing cases must be a JSON object")
    unknown = sorted(only - set(payload))
    if unknown:
        raise ValueError(f"unknown cases: {unknown}; available: {sorted(payload)}")
    selected = {
        str(name): dict(spec)
        for name, spec in payload.items()
        if not only or name in only
    }
    if not selected:
        raise ValueError("no routing eval cases selected")
    return selected


def _backend_up(backend: str) -> bool:
    try:
        with urllib.request.urlopen(backend.rstrip("/") + "/health", timeout=10) as response:
            return response.status == 200
    except OSError:
        return False


def _seed_project(project_dir: Path, spec: dict[str, Any]) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    for relative, content in (spec.get("files") or {}).items():
        target = (project_dir / str(relative)).resolve()
        if project_dir.resolve() not in target.parents:
            raise ValueError(f"eval seed path escapes project: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run live Elira profile/tool/MCP evals through the UI SSE path."
    )
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--only", default="", help="comma-separated case IDs")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "xhigh"),
        default="low",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="client socket timeout in seconds; 0 means no eval-side deadline",
    )
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args()

    if not _backend_up(args.backend):
        print(f"FAIL preflight: backend is unavailable at {args.backend}")
        return 1

    only = {name.strip() for name in args.only.split(",") if name.strip()}
    try:
        cases = _load_cases(args.cases.resolve(), only)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL cases: {exc}")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    suite_id = f"routing-{stamp}"
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else REPO_ROOT / ".agent" / "evals" / "agent-routing" / suite_id
    )
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"Eval output: {output_root}")

    def execute(name: str, spec: dict[str, Any]) -> dict[str, Any]:
        project_dir = output_root / "projects" / name
        _seed_project(project_dir, spec)
        run_id = f"eval-{stamp}-{name}"
        print(f"RUN  {name}: expected profile={spec.get('expected_profile')}")
        try:
            return run_smoke(
                backend=args.backend,
                task_text=str(spec.get("task") or ""),
                project_root=str(project_dir),
                run_id=run_id,
                events_path=output_root / "events" / f"{name}.jsonl",
                timeout_s=args.timeout if args.timeout > 0 else None,
                profile_name=str(spec.get("profile_name") or "Авто"),
                reasoning_effort=args.reasoning_effort,
                permission_mode="bypass",
            )
        except Exception as exc:  # live transport failure belongs in the report
            return {
                "run_id": run_id,
                "stop_reason": "transport-error",
                "error": str(exc)[:500],
                "effective_profile": "",
                "tool_names": [],
                "runtime_operations": [],
                "activated_mcp_server_ids": [],
                "initial_runtime_activation": {},
                "tool_calls": 0,
            }

    report = run_suite(cases, suite_id=suite_id, execute_case=execute)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["backend"] = args.backend
    report["reasoning_effort"] = args.reasoning_effort
    report["cases_path"] = str(args.cases.resolve())
    results_path = output_root / "results.json"
    markdown_path = output_root / "report.md"
    results_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")

    for name, result in report["results"].items():
        summary = result["summary"]
        print(
            f"{result['status']:4} {name}: profile={summary.get('effective_profile') or '—'} "
            f"tools={summary.get('tool_names') or []} "
            f"cache={float(summary.get('cache_hit_ratio') or 0) * 100:.1f}% "
            f"prompt/output={summary.get('prompt_tokens_per_second')}/"
            f"{summary.get('tokens_per_second')} tok/s "
            f"model_ttft={summary.get('model_ttft_ms')}ms "
            f"ttft={summary.get('ttft_s')}s duration={summary.get('duration_s')}s"
        )
        for failure in result["failures"]:
            print(f"     x {failure}")
    aggregate = report["summary"]
    print(
        f"Result: {aggregate['passed']}/{aggregate['total']} PASS; "
        f"profile accuracy={aggregate['profile_accuracy'] * 100:.1f}%"
    )
    print(f"JSON: {results_path}")
    print(f"Markdown: {markdown_path}")
    return 1 if aggregate["failed"] else 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        line_buffering=True,
        write_through=True,
    )
    raise SystemExit(main())
