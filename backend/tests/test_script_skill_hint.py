"""(б) John 2026-10-07: a chat that writes a script hears once that a skill keeps it for next time."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.api.routes.code_agent_routes import _resolve_project_root
from app.application.code_agent import agent_loop, task_skills
from app.application.code_agent.turn_context import TurnContext


def _context(root: Path) -> TurnContext:
    return TurnContext(messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "задача"}],
                       raw_user_message="задача", root=root, working_dir=None, run_id="run")


def test_note_only_for_chat_scripts_outside_the_skills_folder(tmp_path: Path) -> None:
    chat = _context(Path(_resolve_project_root("", "s-9-hint01")))
    project = _context(tmp_path)

    assert project.script_skill_hint(name="write_file", args={"path": "tool.py"}, ok=True) == ""
    assert chat.script_skill_hint(name="write_file", args={"path": "notes.txt"}, ok=True) == ""
    skill_script = str(task_skills.SKILLS_ROOT / "audio-transcribe" / "transcribe.py")
    assert chat.script_skill_hint(name="write_file", args={"path": skill_script}, ok=True) == ""
    assert chat.script_skill_hint(name="write_file", args={"path": "tool.py"}, ok=False) == ""

    note = chat.script_skill_hint(name="write_file", args={"path": "tool.py"}, ok=True)
    assert note.startswith("[Заметка Elira]") and str(task_skills.SKILLS_ROOT) in note
    assert chat.script_skill_hint(name="write_file", args={"path": "other.py"}, ok=True) == ""  # once per run


def _reply(tool=None, arguments=None):
    return {"message": {"content": "" if tool else "Готово.", "tool_calls":
        [{"id": "call", "function": {"name": tool, "arguments": arguments or {}}}] if tool else []}}


def test_real_loop_carries_the_note_in_the_write_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    chat = Path(_resolve_project_root("", "s-9-hint02"))
    seen = []

    def chat_fn(**kwargs):
        if not kwargs.get("tools"):
            return {"message": {"content": "Summary"}}
        seen.append(deepcopy(kwargs["messages"]))
        return _reply("write_file", {"path": "convert.py", "content": "print('ok')\n"}) if len(seen) == 1 else _reply()

    events = list(agent_loop.stream_code_agent(
        user_message="Напиши скрипт конвертации", project_root=chat, chat_fn=chat_fn,
        base_tools=["write_file"], num_ctx=32768, auto_remember=False, permission_mode="bypass"))

    assert events[-1]["stop_reason"] == "answer", events[-1]
    tool_texts = [str(message.get("content")) for message in seen[-1] if message.get("role") == "tool"]
    assert len(tool_texts) == 1 and "[Заметка Elira]" in tool_texts[0]
