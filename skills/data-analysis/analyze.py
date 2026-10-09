"""Explicit scenarios, executed through the existing shell tool."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from cli import main

if __name__ == "__main__":
    raise SystemExit(main({'csv': 'app.application.skill_services.tables:tool_csv'}))
