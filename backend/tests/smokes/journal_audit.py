# -*- coding: utf-8 -*-
"""Audit existing durable agent runs with the live-eval Harness reporter."""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

from driver import summarize_journal


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]


def _select_runs(runs_root: Path, run_ids: list[str], last: int) -> list[Path]:
    if run_ids:
        selected = [runs_root / run_id for run_id in run_ids]
    else:
        selected = sorted(
            (
                path for path in runs_root.iterdir()
                if path.is_dir() and (path / "events.jsonl").is_file()
            ),
            key=lambda path: (path / "events.jsonl").stat().st_mtime,
            reverse=True,
        )[:max(1, last)]
    missing = [path.name for path in selected if not (path / "events.jsonl").is_file()]
    if missing:
        raise FileNotFoundError(f"run journals not found: {', '.join(missing)}")
    return selected


def _findings(summary: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if summary.get("error_prefixed_successes"):
        findings.append(
            f"{summary['error_prefixed_successes']} ERROR-result(s) were marked ok=true"
        )
    if summary.get("partial") or summary.get("state_resumable"):
        findings.append(
            f"terminal state is {summary.get('completion_status') or 'partial'} / resumable"
        )
    tool_tokens = int(summary.get("final_tool_context_tokens") or 0)
    context_tokens = int(summary.get("final_context_tokens") or 0)
    if context_tokens and tool_tokens / context_tokens >= 0.6:
        findings.append(
            f"tool context is {tool_tokens / context_tokens:.0%} of final context"
        )
    if int(summary.get("max_model_ttft_ms") or 0) >= 60_000:
        findings.append(f"max model TTFT is {summary['max_model_ttft_ms']}ms")
    if not summary.get("cached_prompt_tokens_available"):
        findings.append("absolute cached_prompt_tokens are unavailable in this old journal")
    return findings


def render_markdown(summaries: list[dict[str, Any]]) -> str:
    lines = [
        "# Elira durable run audit",
        "",
        "| Run | Profile | State | Steps | Tools | Cache | Max TTFT | Tool context | Findings |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for summary in summaries:
        findings = "; ".join(_findings(summary)) or "—"
        lines.append(
            "| "
            + " | ".join((
                str(summary.get("run_id") or "—"),
                str(summary.get("effective_profile") or "—"),
                str(summary.get("state_status") or summary.get("stop_reason") or "—"),
                str(summary.get("steps") or 0),
                str(summary.get("tool_calls") or 0),
                f"{float(summary.get('cache_hit_ratio') or 0) * 100:.1f}%",
                f"{int(summary.get('max_model_ttft_ms') or 0)}ms",
                str(summary.get("final_tool_context_tokens") or 0),
                findings.replace("|", "\\|"),
            ))
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay durable agent journals through the routing-eval reporter."
    )
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / ".agent" / "runs")
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--last", type=int, default=2)
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args()

    try:
        run_dirs = _select_runs(args.runs_root.resolve(), args.run_id, args.last)
        summaries = [summarize_journal(path) for path in run_dirs]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL journal audit: {exc}")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else REPO_ROOT / ".agent" / "evals" / "run-audit" / stamp
    )
    output_root.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": stamp, "runs": summaries}
    results_path = output_root / "results.json"
    markdown_path = output_root / "report.md"
    results_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        render_markdown(summaries),
        encoding="utf-8",
        newline="\n",
    )
    for summary in summaries:
        print(
            f"RUN {summary['run_id']}: state={summary.get('state_status')} "
            f"steps={summary.get('steps')} tools={summary.get('tool_calls')} "
            f"cache={float(summary.get('cache_hit_ratio') or 0) * 100:.1f}% "
            f"max_ttft={summary.get('max_model_ttft_ms')}ms"
        )
        for finding in _findings(summary):
            print(f"  - {finding}")
    print(f"JSON: {results_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        line_buffering=True,
        write_through=True,
    )
    raise SystemExit(main())
