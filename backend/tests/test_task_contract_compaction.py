"""Compaction keeps the full goal and every requirement out of the lossy summary."""
from __future__ import annotations

from app.application.code_agent.turn_context import TurnContext
from app.application.context.compaction import maybe_compact, TASK_CONTRACT_MARKER_VALUE, TASK_STATE_MARKER_KEY


def test_compaction_keeps_full_goal_and_all_requirements_out_of_lossy_summary(tmp_path):
    context = TurnContext(messages=[{"role": "system", "content": "System"}],
        raw_user_message="Исходная цель с неизменным CSV", root=tmp_path, working_dir=tmp_path, run_id="compact")
    rows = [{"requirement_id": str(i), "text": "Требование " + str(i), "status": "unconfirmed"} for i in range(14)]
    context.refresh_task_state = True
    context.update_task_state(task_spec=None, criteria_rows=rows, checklist_items=[], mutated_files=[],
                              verifications=[], failed_attempts=[])
    original = next(row for row in context.messages if row.get(TASK_STATE_MARKER_KEY) == TASK_CONTRACT_MARKER_VALUE)
    context.messages.extend({"role": "user", "content": "history " + str(i)} for i in range(12))
    summarized = []
    def summary(**kwargs):
        summarized.extend(kwargs["messages"])
        return {"ok": True, "summary": "lossy"}
    packed, compacted = maybe_compact(context.messages, num_ctx=100, model="fixture", chat_fn=None,
                                      summarize_fn=summary, threshold=0, keep_pairs=1)
    assert compacted and original in packed and original not in summarized
    assert "Требование 13" in original["content"] and "Исходная цель" in original["content"]
