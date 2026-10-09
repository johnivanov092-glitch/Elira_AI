"""The SSH instructions are mutable; core prompts contain no injected SSH policy."""
from pathlib import Path
from app.application.code_agent.prompts import _build_system_prompt, _build_turn_context

def test_ssh_instructions_are_selected_as_a_skill():
    root = Path(__file__).resolve().parents[2]
    scenario = (root / "skills/linux-admin/scenarios.md").read_text(encoding="utf-8")
    assert "EncodedCommand" in scenario and "HostName/User/IdentityFile" in scenario
    assert "SSH-ДОСТУПА" not in _build_turn_context(Path("."), task_text="настрой ssh доступ")

def test_normal_task_prompt_stays_task_independent():
    assert _build_system_prompt(Path("."), task_text="") == _build_system_prompt(Path("."), task_text="прочитай main.py")
