"""Deterministic code-agent durability smoke using the real ToolExecutor."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import run_code_agent  # noqa: E402
from app.application.code_agent.run_journal import RunJournal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(BACKEND_ROOT.parent))
    parser.add_argument(
        "--run-id",
        default="runtime-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
    )
    args = parser.parse_args()
    model_calls = 0

    def deterministic_chat(**kwargs):
        nonlocal model_calls
        model_calls += 1
        if kwargs.get("tools") and model_calls == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "run_bash",
                            "arguments": {"command": "git status --short", "timeout": 30},
                        }
                    }],
                }
            }
        return {"message": {"content": "Runtime smoke completed.", "tool_calls": []}}

    result = run_code_agent(
        user_message="Run the deterministic runtime smoke.",
        project_root=Path(args.project_root).resolve(),
        model="smoke-model",
        run_id=args.run_id,
        chat_fn=deterministic_chat,
        auto_remember=False,
    )
    journal = RunJournal.load(args.run_id)
    events = [
        json.loads(line)
        for line in journal.events_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    event_types = {str(event.get("type") or "") for event in events}
    required = {"run_started", "tool_started", "tool_completed", "usage", "run_completed"}
    state = json.loads(journal.state_path.read_text(encoding="utf-8"))
    health = json.loads(journal.health_path.read_text(encoding="utf-8"))
    ok = bool(result.get("ok")) and required.issubset(event_types)
    ok = ok and state.get("status") == "completed" and health.get("status") == "completed"
    print(json.dumps({
        "ok": ok,
        "result": result,
        "state": str(journal.state_path),
        "events": str(journal.events_path),
        "commands": str(journal.commands_path),
        "health": str(journal.health_path),
        "event_types": sorted(event_types),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
