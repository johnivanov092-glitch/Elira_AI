"""Working set of files across compaction (John 2026-10-07, stage 4 / Д)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.application.code_agent import agent_loop
from app.application.code_agent.history import project_runtime_roles
from app.application.code_agent.working_set import WORKING_SET_ID, FileWorkingSet


def _read(working_set: FileWorkingSet, messages: list, path: str, text: str, step: int, **extra) -> str:
    args = {"path": path, **extra}
    stub = working_set.unchanged_read(name="read_file", args=args, ok=True, text=text, messages=messages)
    message = {"role": "tool", "name": "read_file", "content": stub or text}
    messages.append(message)
    if not stub:
        working_set.record(name="read_file", args=args, ok=True, text=text, step=step, message=message)
    return stub


def _text(path: Path) -> str:
    return "".join(f"{index:>5}\t{line}" for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(True), 1))


def test_repeated_read_of_unchanged_file_points_to_the_earlier_result(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('привет')\n", encoding="utf-8")
    working_set, messages = FileWorkingSet(root=tmp_path, working_dir=None), []

    assert _read(working_set, messages, "app.py", "    1\tprint('привет')\n", 2) == ""
    stub = _read(working_set, messages, "app.py", "    1\tprint('привет')\n", 5)

    assert "не менялся с шага 2" in stub and "Перечитывать не нужно" in stub
    assert _read(working_set, messages, "app.py", "    1\tprint('привет')\n", 6, offset=10) == ""


def test_changed_file_compacted_result_and_writes_need_a_full_read(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("a = 1\n", encoding="utf-8")
    working_set, messages = FileWorkingSet(root=tmp_path, working_dir=None), []
    _read(working_set, messages, "app.py", "    1\ta = 1\n", 1)

    target.write_text("a = 2\n", encoding="utf-8")  # changed outside the file tools (run_bash)
    assert _read(working_set, messages, "app.py", "    1\ta = 2\n", 2) == ""

    messages.clear()  # compaction removed the earlier result
    assert _read(working_set, messages, "app.py", "    1\ta = 2\n", 3) == ""

    working_set.record(name="edit_file", args={"path": "app.py"}, ok=True, text="ok", step=4, message={})
    assert _read(working_set, messages, "app.py", "    1\ta = 2\n", 5) == ""


def test_extracted_documents_are_never_answered_from_the_working_set(tmp_path: Path) -> None:
    (tmp_path / "doc.docx").write_bytes(b"PK")
    working_set, messages = FileWorkingSet(root=tmp_path, working_dir=None), []
    text = "[текст извлечён из .docx через file_extract: doc.docx]\n    1\tТекст"
    _read(working_set, messages, "doc.docx", text, 1)

    assert _read(working_set, messages, "doc.docx", text, 2) == ""


def test_block_lists_files_and_carries_recent_changed_text_first(tmp_path: Path) -> None:
    for name in ("notes.md", "main.py", "util.py"):
        (tmp_path / name).write_text(f"# {name}\nстрока\n", encoding="utf-8")
    working_set = FileWorkingSet(root=tmp_path, working_dir=None)
    working_set.record(name="read_file", args={"path": "notes.md"}, ok=True, text="    1\t# notes.md\n",
                       step=1, message={})
    working_set.record(name="write_file", args={"path": "util.py"}, ok=True, text="ok", step=2, message={})
    working_set.record(name="edit_file", args={"path": "main.py"}, ok=True, text="ok", step=3, message={})

    block = working_set.block(num_ctx=65536, read_text=_text)

    assert "Изменены в этой задаче: main.py (шаг 3), util.py (шаг 2)" in block
    assert "Прочитаны: notes.md (шаг 1)" in block
    assert block.index("--- main.py") < block.index("--- util.py") < block.index("--- notes.md")
    assert "    2\tстрока" in block
    small = working_set.block(num_ctx=8192, read_text=_text)
    assert "Изменены в этой задаче" in small and "---" not in small


def test_restore_rebuilds_the_block_only_on_compaction_and_projects_it_as_tool_data(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    working_set = FileWorkingSet(root=tmp_path, working_dir=None)
    working_set.record(name="write_file", args={"path": "main.py"}, ok=True, text="ok", step=1, message={})
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "задача"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c", "function": {"name": "read_file", "arguments": {"path": "main.py"}}}]},
                {"role": "tool", "name": "read_file", "content": "    1\tx = 1\n"}]

    assert working_set.restore(messages, compacted=False, num_ctx=65536, read_text=_text) is messages
    once = working_set.restore(messages, compacted=True, num_ctx=65536, read_text=_text)
    twice = working_set.restore(once, compacted=True, num_ctx=65536, read_text=_text)

    assert sum(message.get("_msg_id") == WORKING_SET_ID for message in twice) == 1
    projected = project_runtime_roles(twice)
    assert "РАБОЧИЙ НАБОР ФАЙЛОВ" not in projected[0]["content"]
    assert "РАБОЧИЙ НАБОР ФАЙЛОВ" in projected[-1]["content"] and projected[-1]["role"] == "tool"


def _reply(tool=None, arguments=None):
    return {"message": {"content": "" if tool else "Готово.", "tool_calls":
        [{"id": "call", "function": {"name": tool, "arguments": arguments or {}}}] if tool else []}}


def test_real_loop_answers_a_repeated_read_without_the_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    (tmp_path / "data.txt").write_text("уникальная строка данных\n", encoding="utf-8")
    seen = []

    def chat(**kwargs):
        if not kwargs.get("tools"):
            return {"message": {"content": "Summary"}}
        seen.append(deepcopy(kwargs["messages"]))
        return _reply("read_file", {"path": "data.txt"}) if len(seen) <= 2 else _reply()

    events = list(agent_loop.stream_code_agent(
        user_message="Прочитай data.txt", project_root=tmp_path, chat_fn=chat,
        base_tools=["read_file"], num_ctx=32768, auto_remember=False, permission_mode="bypass"))

    assert events[-1]["stop_reason"] == "answer", events[-1]
    tool_texts = [str(message.get("content")) for message in seen[-1] if message.get("role") == "tool"]
    assert len(tool_texts) == 2
    assert "уникальная строка данных" in tool_texts[0]
    assert "уникальная строка данных" not in tool_texts[1] and "не менялся" in tool_texts[1]


def test_turn_context_restores_the_working_set_with_real_file_text(tmp_path: Path) -> None:
    from app.application.code_agent.turn_context import TurnContext

    (tmp_path / "main.py").write_text("def handler():\n    return 'ответ'\n", encoding="utf-8")
    context = TurnContext(messages=[{"role": "system", "content": "system"},
                                    {"role": "user", "content": "почини handler"}],
                          raw_user_message="почини handler", root=tmp_path, working_dir=None, run_id="run")
    context.initialize_skills(resume=False)
    context.working_set.record(name="edit_file", args={"path": "main.py"}, ok=True, text="ok", step=3, message={})
    pinned = []

    def prepare_fn(messages, *, pinned_message_ids, restore_messages, **_kwargs):
        pinned.append(set(pinned_message_ids))
        return restore_messages(messages, compacted=True), True, {"percent": 10}

    context.prepare(prepare_fn=prepare_fn, num_ctx=65536, model="m", chat_fn=None, context_profile={},
                    tool_schemas=[], cancel_handle=None, audit_sink=None,
                    restore_source_context=lambda messages, **_kwargs: messages)

    block = next(message["content"] for message in context.messages if message.get("_msg_id") == WORKING_SET_ID)
    assert WORKING_SET_ID in pinned[0]
    assert "Изменены в этой задаче: main.py (шаг 3)" in block
    assert "    2\t    return 'ответ'" in block
