"""A file-read action receipt proves reading, never result semantics."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.code_agent.run_observations import RunObservations
from app.application.code_agent.taskspec import derive_task_spec
from app.application.code_agent.tools._files import tool_read_file


def observed(task: str) -> RunObservations:
    return RunObservations(task_spec=derive_task_spec(task), durable_state={}, resume=False)


def feed(observations: RunObservations, path: str, output: dict, *, root: Path | None = None) -> None:
    if root is not None:
        observations.before_dispatch("read_file", {"path": path}, root=root)
    observations.complete_result(name="read_file", args={"path": path}, output=output,
        status="ok" if output["ok"] else "error", text=output.get("text", ""), ok=output["ok"],
        state_changed=False, verification="")


def test_successful_canonical_read_confirms_only_the_named_read_action(tmp_path):
    (tmp_path / "source.txt").write_text("total=wrong\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read\n- Total is correct")
    feed(observations, "source.txt", tool_read_file(tmp_path, path="source.txt"))
    assert [item["status"] for item in observations.criteria.items] == ["confirmed", "unconfirmed"]
    assert observations.criteria.items[0]["target"] == "source.txt"
    assert observations.criteria.items[0]["evidence"] == "source.txt"


def test_read_failure_wrong_target_and_raw_success_text_are_not_read_proof(tmp_path):
    (tmp_path / "other.txt").write_text("contents\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read")
    feed(observations, "source.txt", tool_read_file(tmp_path, path="source.txt"))
    feed(observations, "other.txt", tool_read_file(tmp_path, path="other.txt"))
    feed(observations, "source.txt", {"ok": True, "text": "Source is read"})
    assert observations.criteria.items[0]["status"] == "unconfirmed"


def test_a_named_other_file_cannot_borrow_the_goal_target(tmp_path):
    (tmp_path / "source.txt").write_text("contents\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- File other.txt is read")
    feed(observations, "source.txt", tool_read_file(tmp_path, path="source.txt"))
    assert observations.criteria.items[0]["status"] == "unconfirmed"


def test_complete_read_action_stays_unconfirmed_for_a_truncated_range(tmp_path):
    (tmp_path / "source.txt").write_text("a\nb\nc\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read completely")
    feed(observations, "source.txt", tool_read_file(tmp_path, path="source.txt", limit=1))
    assert observations.criteria.items[0]["status"] == "unconfirmed"
    feed(observations, "source.txt", tool_read_file(tmp_path, path="source.txt"))
    assert observations.criteria.items[0]["status"] == "confirmed"


def test_a_tail_read_does_not_prove_a_complete_file_read(tmp_path):
    (tmp_path / "source.txt").write_text("a\nb\nc\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read completely")
    output = tool_read_file(tmp_path, path="source.txt", offset=2)
    observations.complete_result(name="read_file", args={"path": "source.txt", "offset": 2},
        output=output, status="ok", text=output["text"], ok=True, state_changed=False, verification="")
    assert observations.criteria.items[0]["status"] == "unconfirmed"


@pytest.mark.parametrize("absolute", [False, True])
def test_read_receipt_resolves_the_exact_named_path_against_the_known_root(tmp_path, absolute):
    source = tmp_path / "source.txt"
    source.write_text("contents\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read completely")
    requested = str(source) if absolute else "source.txt"
    feed(observations, requested, tool_read_file(tmp_path, path=requested), root=tmp_path)
    assert observations.criteria.items[0]["status"] == "confirmed"
    assert observations.criteria.items[0]["bound_target"] == str(source.resolve()).replace("\\", "/").lower()


@pytest.mark.parametrize("location", ["other/source.txt", "../outside/source.txt"])
def test_same_basename_in_a_different_directory_is_not_the_named_read_action(tmp_path, location):
    project = tmp_path / "project"
    project.mkdir()
    other = (project / location).resolve()
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("different contents\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read completely")
    requested = str(other)
    feed(observations, requested, tool_read_file(tmp_path, path=requested), root=project)
    assert observations.criteria.items[0]["status"] == "unconfirmed"
    assert observations.criteria.items[0]["bound_target"] == ""


def test_a_bound_read_object_does_not_follow_a_later_project_root(tmp_path):
    original = tmp_path / "original"
    changed = tmp_path / "changed"
    for root in (original, changed):
        root.mkdir()
        (root / "source.txt").write_text("contents\n", encoding="utf-8")
    observations = observed("Read source.txt.\nSuccess criteria:\n- Source is read completely")
    feed(observations, "source.txt", tool_read_file(original, path="source.txt"), root=original)
    observations.criteria.invalidate_after_mutation()
    feed(observations, "source.txt", tool_read_file(changed, path="source.txt"), root=changed)
    assert observations.criteria.items[0]["status"] == "unconfirmed"
    feed(observations, str(original / "source.txt"),
         tool_read_file(tmp_path, path=str(original / "source.txt")), root=changed)
    assert observations.criteria.items[0]["status"] == "confirmed"
