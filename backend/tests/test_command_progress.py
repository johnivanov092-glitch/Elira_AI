from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.command_progress import CommandProgress, command_digest


def _job(job_id: str, *, status: str = "completed", content: str = "rows=1416", **changes) -> dict:
    return {
        "action": "logs", "kind": "job", "job_id": job_id,
        "status": status, "exit_code": None if status == "running" else 0,
        "command_sha256": command_digest("python transform.py"),
        "cwd": "D:/work", "output_sha256": hashlib.sha256(content.encode()).hexdigest(),
        **changes,
    }


def test_repeated_completed_attempts_ignore_pid_wrappers_and_survive_resume() -> None:
    tracker = CommandProgress()
    first = _job("a", pid=123, text="pid=123 age=1s rows=1416")
    assert tracker.observe("run_server", {}, first, epoch=7) is None
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    # Polling the terminal job is still observation, including after Resume.
    assert tracker.observe("run_server", {}, first, epoch=7) is None
    hint = tracker.observe("run_server", {}, _job("b", pid=789, text="pid=789 age=2s rows=1416"), epoch=7)
    assert hint is not None and "exit=0" in hint
    assert "2 отдельных запуска" in hint
    # The same diagnostic is not injected on every subsequent tool call.
    assert tracker.observe("run_server", {}, _job("c"), epoch=7) is None
    assert tracker.observe("run_server", {}, first, epoch=7) is None


def test_live_polling_new_output_and_terminal_transition_are_not_retries() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a", status="running")) is None
    assert tracker.observe("run_server", {}, _job("a", status="running")) is None
    assert tracker.observe("run_server", {}, _job("a", status="running", content="rows=354")) is None
    assert tracker.observe("run_server", {}, _job("a", content="rows=354")) is None
    assert tracker.observe("run_server", {}, _job("a", content="rows=354")) is None
    # New actual output is information, even if the command is unchanged.
    assert tracker.observe("run_server", {}, _job("b", content="rows=355")) is None
    assert tracker.observe("run_server", {}, _job("c", content="rows=355")) is not None


def test_active_recovery_context_survives_resume_until_result_or_epoch_changes() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), epoch=7) is None
    hint = tracker.observe("run_server", {}, _job("b"), epoch=7)
    assert hint and tracker.context() == hint
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    assert tracker.context() == hint
    assert tracker.observe("run_server", {}, _job("b"), epoch=7) is None
    assert tracker.observe("run_server", {}, _job("c"), epoch=7) is None
    assert tracker.context() == hint
    assert tracker.observe("run_server", {}, _job("d", content="rows=270"), epoch=7) is None
    assert tracker.context() == ""
    assert tracker.observe("run_server", {}, _job("e", content="rows=270"), epoch=7)
    assert tracker.context()
    # Epoch synchronization on a non-command observation also retires advice.
    assert tracker.observe("write_file", {}, {}, epoch=8) is None
    assert tracker.context() == ""
    assert CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot()))).context() == ""


def test_input_code_epoch_and_working_directory_separate_attempts() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), epoch="input-v1") is None
    assert tracker.observe("run_server", {}, _job("b"), epoch="input-v2") is None
    assert tracker.observe("run_server", {}, _job("c", cwd="D:/other"), epoch="input-v2") is None
    # A job started before a mutation cannot prove the new input/code version.
    assert tracker.observe("run_server", {}, _job("old", status="running"), epoch="input-v2") is None
    assert tracker.observe("run_server", {}, _job("old"), epoch="input-v3") is None
    assert tracker.observe("run_server", {}, _job("new"), epoch="input-v3") is None
    assert tracker.observe("run_server", {}, _job("again"), epoch="input-v3") is not None


def test_commands_business_numbers_and_terminal_states_remain_distinct() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a", status="failed", exit_code=2)) is None
    assert tracker.observe("run_server", {}, _job("b")) is None
    assert tracker.observe("run_server", {}, _job("c", command_sha256=command_digest("python transform.py --week 2"))) is None
    assert tracker.observe("run_server", {}, _job("d", content="rows=1417")) is None
    assert command_digest("", argv=["python", "-c", "print(1)"]) != command_digest("", argv=["python", "-c", "print(2)"])


def test_incomplete_or_rejected_receipts_are_not_inferred_from_text() -> None:
    tracker = CommandProgress()
    assert tracker.observe("run_server", {}, _job("a"), execution_status="blocked") is None
    incomplete = _job("a", output_sha256=None, text="False ERROR exit=0")
    assert tracker.observe("run_server", {}, incomplete) is None
    # A later poll can provide a sealed log after the writer finishes flushing.
    assert tracker.observe("run_server", {}, _job("a")) is None
    assert tracker.observe("run_server", {}, _job("b")) is not None
    restored = CommandProgress.from_snapshot({"version": 1, "results": ["invalid"], "attempts": False})
    assert restored.observe("run_server", {}, _job("a")) is None


def test_sandbox_checks_structured_outputs_without_parsing_false() -> None:
    tracker = CommandProgress()
    arguments = {"code": "print(False)"}
    output = {"ok": True, "exit_code": 0, "stdout": "False\n", "stderr": ""}
    assert tracker.observe("sandbox_run", arguments, output, cwd="D:/work") is None
    hint = tracker.observe("sandbox_run", arguments, output, cwd="D:/work")
    assert hint is not None and "exit=0" in hint


def test_unchanged_commands_require_new_diagnosis_and_bound_rechecks_across_resume() -> None:
    tracker = CommandProgress()
    args = {"command": "find evidence"}
    output = {"exit_code": 0, "stdout": "", "stderr": ""}
    for _ in range(3):
        assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
        tracker.observe("run_bash", args, output, cwd="D:/work")
    recovery = tracker.before_dispatch("run_bash", args, cwd="D:/work")
    assert recovery["status"] == "recovery_required"
    assert recovery["observed_attempts"] == 3
    assert recovery["business_outcome"] == "not_assessed"
    tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    for diagnostic in ("first observation", "external state changed"):
        tracker.observe("read_file", {"path": "input.txt"}, {"ok": True, "text": diagnostic})
        assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
        tracker.observe("run_bash", args, output, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "blocked"
    # Actual input-version change authorizes work again, including after Resume.
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work", epoch=1) is None


def test_replayed_diagnostics_do_not_unlock_commands_but_changed_results_do() -> None:
    tracker = CommandProgress()
    args = {"command": "observe service"}
    output = {"exit_code": 0, "stdout": "waiting", "stderr": ""}
    for _ in range(3):
        tracker.observe("run_bash", args, output, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "recovery_required"
    tracker.observe("read_file", {"path": "service.log"}, {"text": "waiting"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    tracker.observe("run_bash", args, output, cwd="D:/work")
    tracker.observe("read_file", {"path": "service.log"}, {"text": "waiting"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "recovery_required"
    # Model-written strategy claims and failed diagnostic calls are not evidence.
    tracker.observe("runtime_control", {"operation": "task_decide"}, {"ok": True, "text": "new plan"})
    tracker.observe("read_file", {"path": "service.log"}, {"ok": False, "text": "missing"})
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work")["status"] == "blocked"
    tracker.observe("read_file", {"path": "service.log"}, {"text": "ready"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    tracker.observe("run_bash", args, {**output, "stdout": "ready"}, cwd="D:/work")
    assert tracker.before_dispatch("run_bash", args, cwd="D:/work") is None
    assert tracker.context() == ""


def test_recovery_never_blocks_polling_an_existing_job_or_another_directory() -> None:
    tracker = CommandProgress()
    for identity in ("a", "b", "c"):
        tracker.observe("run_server", {}, _job(identity))
    args = {"action": "start", "command": "python transform.py"}
    assert tracker.before_dispatch("run_server", args, cwd="D:/work")["status"] == "recovery_required"
    for _ in range(10):
        assert tracker.before_dispatch("run_server", {"action": "logs", "pid": 123}, cwd="D:/work") is None
        tracker.observe("run_server", {}, _job("a"))
    assert tracker.before_dispatch("run_server", args, cwd="D:/other") is None


def test_kernel_failed_process_repeats_but_rejected_receipts_do_not_count(tmp_path: Path) -> None:
    from app.application.agent_kernel import executor
    from app.application.code_agent.tools import _run

    command = f'"{sys.executable}" -c "import sys; print(1416); sys.exit(2)"'
    request = executor.ToolExecutionRequest(
        run_id="command-progress-failure", agent_id="code_agent", project_scope_id="",
        tool_name="run_bash", args={"command": command}, source="code_agent",
        permission_mode="bypass",
    )
    tracker = CommandProgress()
    with mock.patch("app.application.tool_registry.runtime.get_tool", return_value=None), \
            mock.patch.object(executor, "_emit_executed"):
        first = executor.execute_tool(request, lambda _name, args: _run.tool_run_bash(tmp_path, **args))
        assert first.status == "error" and first.output["exit_code"] == 2
        for status in ("rejected", "waiting_approval", "cancelled"):
            assert tracker.observe("run_bash", request.args, first.output, execution_status=status) is None
        assert tracker.snapshot()["results"] == {}
        assert tracker.observe("run_bash", request.args, first.output, execution_status=first.status) is None

        second = executor.execute_tool(request, lambda _name, args: _run.tool_run_bash(tmp_path, **args))
        hint = tracker.observe("run_bash", request.args, second.output, execution_status=second.status)
        assert hint is not None and "exit=2" in hint and "2 отдельных запуска" in hint


def test_real_command_and_durable_background_job_produce_stable_observations(tmp_path: Path) -> None:
    from app.application.code_agent.tools import _background_jobs, _run

    command = f'"{sys.executable}" -c "print(1416)"'
    direct = _run.tool_run_bash(tmp_path, command=command)
    assert direct["exit_code"] == 0
    assert direct["cwd"] == str(tmp_path.resolve())
    assert direct["command_sha256"] == command_digest(command)
    tracker = CommandProgress()
    assert tracker.observe("run_bash", {"command": command}, direct) is None
    assert tracker.observe("run_bash", {"command": command}, _run.tool_run_bash(tmp_path, command=command)) is not None

    argv = [sys.executable, "-c", "print(1416)"]
    with mock.patch.object(_background_jobs, "_state_dir", return_value=tmp_path / "jobs"):
        results = []
        for index in range(2):
            started = _run.start_background_argv_job(tmp_path, argv=argv, display_command="python probe")
            assert started["command_sha256"] == command_digest("python probe", argv=argv)
            result = started
            deadline = time.monotonic() + 10
            while result["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.05)
                result = _run.tool_run_server(tmp_path, action="logs", kind="job", pid=started["pid"])
            assert result["status"] == "completed", result
            assert result["output_sha256"] == hashlib.sha256(Path(result["log_path"]).read_bytes()).hexdigest()
            results.append(result)
        assert results[0]["job_id"] != results[1]["job_id"]
        assert results[0]["command_sha256"] == results[1]["command_sha256"]
        assert results[0]["output_sha256"] == results[1]["output_sha256"]
        assert tracker.observe("run_server", {}, results[0]) is None
        assert tracker.observe("run_server", {}, results[1]) is not None
        # Recovered handle obtains command/cwd identity from the existing journal.
        with _run._SERVERS_LOCK:
            for result in results:
                _run._LIVE_SERVERS.pop(result["pid"], None)
        _run.recover_background_jobs()
        recovered = _run.tool_run_server(tmp_path, action="logs", kind="job", pid=results[1]["pid"])
        assert recovered["command_sha256"] == results[1]["command_sha256"]
        assert recovered["cwd"] == str(tmp_path.resolve())
        with _run._SERVERS_LOCK:
            for result in results:
                _run._LIVE_SERVERS.pop(result["pid"], None)


def _search_output(text: str = "Found 3 results across 4 parallel queries") -> dict:
    return {"ok": True, "text": text, "sources": []}


def test_identical_web_search_requires_recovery_and_survives_resume():
    progress = CommandProgress()
    args = {"queries": ["Казахстан ЧП 27 сентября 2026"], "categories": "news"}
    hints = [progress.observe("web_search", args, _search_output(), epoch=0, cwd="D:/work") for _ in range(5)]
    assert hints[0] is None
    assert all("ПОВТОР БЕЗ НОВЫХ ДАННЫХ" in hint and "не остановка задачи" in hint for hint in hints[1:])
    assert "5-й раз" in hints[4]
    recovery = progress.before_dispatch("web_search", args, cwd="D:/work", epoch=0)
    assert recovery["status"] == "recovery_required"
    progress = CommandProgress.from_snapshot(json.loads(json.dumps(progress.snapshot())))
    assert progress.context()
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "strategy_required"
    assert progress.exhausted_web_tools() == {"web_search"}
    assert CommandProgress.from_snapshot(progress.snapshot()).exhausted_web_tools() == {"web_search"}
    # A different query, a page read and productive coding are still available.
    assert progress.before_dispatch("web_search", {"query": "another question"}, cwd="D:/work") is None
    assert progress.before_dispatch("web_fetch", {"url": "https://example.org"}, cwd="D:/work") is None
    for index in range(250):
        assert progress.before_dispatch("run_bash", {"command": f"check part {index}"}, cwd="D:/work") is None
    # New direct user input makes a fresh observation meaningful again.
    assert progress.before_dispatch("web_search", args, cwd="D:/work", epoch=1) is None


def test_changed_web_result_or_arguments_reset_the_repeat_count():
    progress = CommandProgress()
    args = {"query": "SearXNG keep_only"}
    assert progress.observe("web_fetch", args, _search_output("A"), epoch=0, cwd="D:/work") is None
    assert progress.observe("web_fetch", args, _search_output("A"), epoch=0, cwd="D:/work") is not None
    assert progress.observe("web_fetch", args, _search_output("B"), epoch=0, cwd="D:/work") is None
    assert progress.observe("web_fetch", args, _search_output("B"), epoch=0, cwd="D:/work") is not None
    other = {"query": "SearXNG engines"}
    assert progress.observe("web_query", other, _search_output("B"), epoch=0, cwd="D:/work") is None
    failed = {"ok": False, "text": "ERROR: web search unavailable"}
    for _ in range(3):
        progress.observe("web_search", args, failed, execution_status="error", cwd="D:/work")
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "recovery_required"


@pytest.mark.parametrize("read_tool", ["web_fetch", "web_query"])
def test_new_read_evidence_allows_one_failed_search_probe_without_escalation(read_tool):
    progress = CommandProgress()
    args = {"query": "source A"}
    failed = {"ok": False, "text": "ERROR: search timed out"}
    for _ in range(2):
        assert progress.before_dispatch("web_search", args, cwd="D:/work") is None
        progress.observe("web_search", args, failed, execution_status="error")
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "recovery_required"
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "strategy_required"
    progress.observe(read_tool, {"url": "https://example.org/new"}, _search_output("new facts"))
    progress = CommandProgress.from_snapshot(progress.snapshot())
    assert progress.before_dispatch("web_search", args, cwd="D:/work") is None
    progress.observe("web_search", args, failed, execution_status="error")
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "recovery_required"
    assert progress.before_dispatch("web_search", args, cwd="D:/work")["status"] == "strategy_required"


def test_web_reordering_and_receipt_timestamps_do_not_create_new_evidence():
    progress = CommandProgress()
    urls = ["https://example.org/a", "https://example.org/b"]
    for index in range(3):
        batch = urls if index % 2 else list(reversed(urls))
        output = {"ok": True, "text": f"volatile engine warning {index}", "sources": [
            {"url": url, "status": "excerpt", "content_hash": "same-page", "fetched_at": index}
            for url in batch]}
        progress.observe("web_fetch", {"urls": batch}, output)
    args = {"urls": urls}
    assert progress.before_dispatch("web_fetch", args, cwd="")["status"] == "recovery_required"
    # A genuinely changed page still resets its evidence count.
    output["sources"][0]["content_hash"] = "updated-page"
    progress.observe("web_fetch", args, output)
    assert not progress.context()
    assert not progress.exhausted_web_tools()
    repeat = progress.before_dispatch("web_fetch", args, cwd="")
    assert repeat["observed_attempts"] == 1 and repeat["status"] == "recovery_required"


def test_success_is_reused_immediately_but_transient_failure_gets_one_retry():
    for ok, network_attempts in ((True, 1), (False, 2)):
        tracker = CommandProgress()
        args = {"url": "https://example.org/article"}
        for _ in range(network_attempts):
            assert tracker.before_dispatch("web_fetch", args, cwd="") is None
            tracker.observe("web_fetch", args, {"ok": ok, "text": "content" if ok else "timeout"},
                            execution_status="ok" if ok else "error")
        restored = CommandProgress.from_snapshot(tracker.snapshot())
        assert restored.before_dispatch("web_fetch", args, cwd="")["status"] == "recovery_required"


@pytest.mark.parametrize("tool,args", [
    ("web_search", {"query": "original source"}),
    ("web_fetch", {"url": "https://example.org/original"}),
    ("web_query", {"query": "original source", "doc_id": "original"}),
])
@pytest.mark.parametrize("resume", [False, True])
def test_new_other_observation_does_not_authorize_successful_web_repeat(tool, args, resume):
    tracker = CommandProgress()
    assert tracker.before_dispatch(tool, args, cwd="D:/work") is None
    tracker.observe(tool, args, {"ok": True, "text": "Original evidence",
                               "sources": [{"id": "source-original"}]}, cwd="D:/work")
    original_revision = tracker.snapshot()["revision"]
    other = {"query": "different source"}
    assert tracker.before_dispatch("web_search", other, cwd="D:/work") is None
    tracker.observe("web_search", other, _search_output("Different evidence"), cwd="D:/work")
    assert tracker.snapshot()["revision"] > original_revision
    if resume:
        tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    repeat = tracker.before_dispatch(tool, args, cwd="D:/work")
    assert repeat is not None and repeat["reason"] == "repeated_web_without_progress"
    assert repeat["observed_attempts"] == 1
    if tool in {"web_fetch", "web_query"}:
        assert tracker.cached_source_ids(tool, args, epoch=0) == ["source-original"]
    different = {**args, "query": "another question"} if tool != "web_fetch" else {
        "url": "https://example.org/another"}
    assert tracker.before_dispatch(tool, different, cwd="D:/work") is None
    if tool == "web_fetch":
        assert tracker.before_dispatch(tool, {**args, "force_refresh": True}, cwd="D:/work") is None
    assert tracker.before_dispatch(tool, args, cwd="D:/work", epoch="changed-input-artifact") is None


def test_rephrasing_with_identical_evidence_does_not_reset_recovery():
    tracker = CommandProgress()
    args = {"query": "original question"}
    result = {"ok": True, "text": "The same evidence"}
    for _ in range(3):
        tracker.observe("web_search", args, result, cwd="D:/work")
    tracker.before_dispatch("web_search", args, cwd="D:/work")
    tracker.before_dispatch("web_search", args, cwd="D:/work")
    revision = tracker.snapshot()["revision"]
    tracker.observe("web_search", {"query": "rephrased question"}, result, cwd="D:/work")
    assert tracker.snapshot()["revision"] == revision
    assert tracker.exhausted_web_tools() == {"web_search"}
    assert tracker.before_dispatch("web_search", args, cwd="D:/work") is not None
    tracker.observe("web_fetch", {"url": "https://example.org/new"},
                    {"ok": True, "text": "New evidence"}, cwd="D:/work")
    assert tracker.exhausted_web_tools() == {"web_search"}
    assert tracker.before_dispatch("web_search", args, cwd="D:/work") is not None
    assert tracker.before_dispatch("web_search", {"query": "a different source"}, cwd="D:/work") is None

def test_redirect_nonce_is_not_fresh_evidence_but_changed_text_is():
    tracker = CommandProgress()
    args = {"url": "https://example.org/article#methods"}
    for nonce in ("one", "two", "three"):
        result = {"ok": True, "sources": [{"url": f"https://example.org/article?code={nonce}#methods",
            "status": "excerpt", "content_hash": "a" * 64, "excerpt_hash": "b" * 64}]}
        tracker.observe("web_fetch", args, result, cwd="D:/work")
    assert tracker.snapshot()["revision"] == 1
    assert tracker.before_dispatch("web_fetch", args, cwd="D:/work")["status"] == "recovery_required"
    assert tracker.before_dispatch("web_fetch", args, cwd="D:/work")["status"] == "strategy_required"
    result["sources"][0]["excerpt_hash"] = "c" * 64
    tracker.observe("web_fetch", {"url": "https://example.org/other"}, result, cwd="D:/work")
    assert tracker.snapshot()["revision"] == 2
    assert tracker.before_dispatch("web_fetch", args, cwd="D:/work") is not None
    assert tracker.before_dispatch("web_fetch", {**args, "force_refresh": True}, cwd="D:/work") is None


def test_failed_passive_browser_repeats_recover_but_interaction_and_success_do_not():
    for args, ok, guarded in (({"url": "https://example.org"}, False, True),
                             ({"url": "https://example.org", "actions": [{"click": "Next"}]}, False, False),
                             ({"url": "https://example.org", "viewport": "mobile"}, False, False),
                             ({"url": "https://example.org"}, True, False)):
        tracker = CommandProgress()
        for _ in range(2):
            assert tracker.before_dispatch("browser", args, cwd="") is None
            tracker.observe("browser", args, {"ok": ok, "text": "visible text" if ok else "timeout"},
                            execution_status="ok" if ok else "error")
        assert bool(tracker.before_dispatch("browser", args, cwd="")) is guarded
        tracker.before_dispatch("browser", args, cwd="")
        assert "browser" not in tracker.exhausted_web_tools()


@pytest.mark.parametrize("resume", [False, True])
def test_other_evidence_preserves_successful_read_source_handles(resume):
    tracker = CommandProgress()
    args = {"url": "https://example.org/a"}
    tracker.observe("web_fetch", args, {"ok": True, "text": "A", "sources": [{"id": "source-a"}]})
    assert tracker.cached_source_ids("web_fetch", args, epoch=0) == ["source-a"]
    tracker.observe("web_fetch", {"url": "https://example.org/b"}, {"ok": True, "text": "B"})
    if resume:
        tracker = CommandProgress.from_snapshot(json.loads(json.dumps(tracker.snapshot())))
    assert tracker.cached_source_ids("web_fetch", args, epoch=0) == ["source-a"]
    assert tracker.before_dispatch("web_fetch", args, cwd="")["reason"] == "repeated_web_without_progress"


@pytest.mark.parametrize("mime,blocked", [("application/pdf", True), ("text/html", False)])
def test_only_observed_whole_pdf_anchors_are_aliases_across_resume(mime, blocked):
    tracker = CommandProgress()
    url = "https://example.org/download"
    tracker.observe("web_fetch", {"url": url + "#page=1"}, {"ok": True, "text": "Document text",
        "pages": [{"url": url + "#page=1", "final_url": url, "mime": mime}]})
    restored = CommandProgress.from_snapshot(tracker.snapshot())
    assert bool(restored.before_dispatch("web_fetch", {"url": url + "#page=24"}, cwd="")) is blocked
    assert restored.before_dispatch("browser", {"url": url + "#page=24"}, cwd="") is None
    assert restored.before_dispatch("web_fetch", {"url": "https://example.org/other#page=1"}, cwd="") is None


@pytest.mark.parametrize("resume", [False, True])
def test_pdf_alias_detection_leaves_malformed_urls_to_tool_validation(resume):
    tracker = CommandProgress()
    url, malformed = "https://example.org/download", "http://[invalid#page=2"
    tracker.observe("web_fetch", {"url": url}, {"ok": True, "text": "Document text",
        "pages": [{"url": url, "final_url": url, "mime": "application/pdf"},
                  {"url": malformed, "mime": "application/pdf"}]})
    if resume:
        tracker = CommandProgress.from_snapshot(tracker.snapshot())
    assert tracker.snapshot()["whole_documents"] == [url]
    for args in ({"url": malformed}, {"urls": [malformed, url + "#page=2"]}):
        assert tracker.before_dispatch("web_fetch", args, cwd="") is None
        assert tracker.cached_source_ids("web_fetch", args, epoch=0) == []
        tracker.observe("web_fetch", args, {"ok": False, "text": "Invalid URL"}, execution_status="error")


def test_http_failure_redirect_variants_do_not_reset_identical_operation_retry():
    tracker = CommandProgress()
    args = {"url": "https://example.org/paper", "find": "children"}
    for final in (args["url"], args["url"] + "?cookiesEnabled"):
        assert tracker.before_dispatch("web_fetch", args, cwd="") is None
        tracker.observe("web_fetch", args, {"ok": False, "text": "HTTP 429: " + final,
            "pages": [{"url": args["url"], "final_url": final, "status_code": 429}],
            "sources": [{"status": "failed", "url": final, "error": "HTTP 429: " + final}]}, execution_status="error")
    assert tracker.before_dispatch("web_fetch", args, cwd="")["status"] == "recovery_required"


@pytest.mark.parametrize("tool", ["web_fetch", "browser"])
def test_first_failed_passive_read_guides_fallback_without_treating_error_as_article(tool):
    tracker = CommandProgress()
    args = {"url": "https://example.org/found-article"}
    output = {"ok": False, "text": "ERROR: HTTP 403", "pages": [{"url": args["url"], "status_code": 403}]}
    hint = tracker.observe(tool, args, output, execution_status="error")
    assert hint and "не являются содержимым статьи" in hint
    assert "точный URL" in hint and "не угадывай" in hint and "пробел" in hint
    assert tracker.snapshot()["revision"] == 0
    # Advice is immediate; the existing bounded transient retry still works.
    assert tracker.before_dispatch(tool, args, cwd="") is None
    tracker.observe(tool, args, output, execution_status="error")
    restored = CommandProgress.from_snapshot(tracker.snapshot())
    refusal = restored.before_dispatch(tool, args, cwd="")
    assert refusal["status"] == "recovery_required"
    assert "не являются содержимым статьи" in refusal["text"]
    assert "используй результат предыдущего вызова" not in refusal["text"]
    assert restored.before_dispatch(tool, {"url": "https://example.org/another-found"}, cwd="") is None


@pytest.mark.parametrize("args", [
    {"url": "https://example.org", "actions": [{"click": "Next"}]},
    {"url": "https://example.org", "viewport": "mobile"},
])
def test_failed_interactive_browser_does_not_receive_passive_read_advice(args):
    tracker = CommandProgress()
    assert tracker.observe("browser", args, {"ok": False, "text": "Interaction failed"},
                           execution_status="error") is None
    assert tracker.before_dispatch("browser", args, cwd="") is None
