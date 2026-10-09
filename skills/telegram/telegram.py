"""Explicit scenarios, executed through the existing shell tool."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main({'send': 'app.application.skill_services.telegram_actions:send', 'messages': 'app.application.skill_services.telegram_actions:messages'}))
