from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.taskspec import CriteriaTracker, TaskSpec


def _receipt(tmp_path: Path, checks: list[dict], *, status: str = "passed", exit_code: int = 0,
             target: str = "result.csv") -> dict:
    artifact = tmp_path / target
    artifact.write_bytes(b"rows=270\n")
    return {"ok": status == "passed", "result": {"verification": {
        "kind": "command_check", "status": status, "command": "python verify.py",
        "exit_code": exit_code, "checks": checks,
        "targets": [{"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
        "report_path": str(tmp_path / "check.json"), "report_sha256": "a" * 64,
    }}}


def _record(tracker: CriteriaTracker, meta: dict) -> bool:
    return tracker.record(tool_name="runtime_control", args={"operation": "result_verify"},
                          ok=meta["ok"], evidence="runtime result checker", meta=meta)


def test_explicit_check_confirms_only_exact_named_target_criterion(tmp_path: Path) -> None:
    name = "result.csv содержит 270 операций"
    tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=[name, "Суммы рассчитаны с точностью 0.001"]))
    assert not _record(tracker, _receipt(tmp_path, [{"name": "all good", "passed": True}]))
    assert not _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}], target="other.csv"))
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}]))
    assert [row["status"] for row in tracker.report()] == ["confirmed", "unconfirmed"]
    # Decimal quantities in an exact named check are not file names.
    assert _record(tracker, _receipt(tmp_path, [{"name": "Суммы рассчитаны с точностью 0.001", "passed": True}]))
    assert tracker.completion_status() == "confirmed"


def test_later_failed_or_inconclusive_named_check_revokes_pass(tmp_path: Path) -> None:
    name = "result.csv содержит 270 операций"
    tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=[name]))
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}]))
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": False}], status="failed"))
    assert tracker.completion_status() == "failed"
    assert not _record(tracker, _receipt(tmp_path, [{"name": name, "passed": False}], status="failed"))
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}], status="unverified"))
    assert tracker.completion_status() == "unverified"
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}]))
    assert tracker.completion_status() == "confirmed"


def test_exit_zero_without_named_boolean_check_does_not_confirm(tmp_path: Path) -> None:
    name = "result.csv содержит 270 операций"
    tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=[name]))
    assert not _record(tracker, {"ok": True, "exit_code": 0, "text": "all checks passed"})
    for checks in ([], [{"name": name, "passed": "true"}],
                   [{"name": name, "passed": True}, {"name": name, "passed": True}]):
        assert not _record(tracker, _receipt(tmp_path, checks))
    assert not _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}], exit_code=1))
    assert tracker.completion_status() == "unverified"


def test_check_for_one_file_cannot_confirm_multi_file_criterion(tmp_path: Path) -> None:
    name = "result.csv и summary.csv сверены"
    tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=[name]))
    assert not _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}]))
    assert tracker.completion_status() == "unverified"


def test_explicit_absolute_path_is_not_satisfied_by_another_same_named_file(tmp_path: Path) -> None:
    name = f"{(tmp_path / 'result.csv').as_posix()} содержит 270 операций"
    tracker = CriteriaTracker.from_spec(TaskSpec(success_criteria=[name]))
    other = tmp_path / "other"
    other.mkdir()
    assert not _record(tracker, _receipt(other, [{"name": name, "passed": True}]))
    assert _record(tracker, _receipt(tmp_path, [{"name": name, "passed": True}]))
