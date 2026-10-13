from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _import_path in (_REPO_ROOT / "backend", _REPO_ROOT / "skills/math", _REPO_ROOT / "skills/web-research"):
    if str(_import_path) not in sys.path:
        sys.path.insert(0, str(_import_path))

_LOCAL_PROVIDER_DEFAULTS = {
    "LLAMA_SERVER_ENABLED": "false",
    "LOCAL_EMBED_ENABLED": "false",
    "LOCAL_RERANK_ENABLED": "false",
}

os.environ["ELIRA_SKILLS_ROOT"] = str(_REPO_ROOT / "skills")

_TEST_DATA_DIR = tempfile.TemporaryDirectory(prefix="elira-pytest-data-")
os.environ["ELIRA_DATA_DIR"] = _TEST_DATA_DIR.name
os.environ["ELIRA_AGENT_RUNS_DIR"] = os.path.join(_TEST_DATA_DIR.name, "agent-runs")

# Tests import route modules during collection; those imports can load
# backend/.env.local via app.main. Pin these first so load_dotenv(...,
# override=False) cannot leak a developer's live local providers into the suite.
for _key, _value in _LOCAL_PROVIDER_DEFAULTS.items():
    os.environ[_key] = _value


@pytest.fixture(autouse=True)
def isolate_local_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _LOCAL_PROVIDER_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ELIRA_DATA_DIR", os.environ["ELIRA_DATA_DIR"])
    monkeypatch.setenv("ELIRA_AGENT_RUNS_DIR", os.environ["ELIRA_AGENT_RUNS_DIR"])


@pytest.fixture(autouse=True)
def isolated_web_skill_state(tmp_path, monkeypatch):
    from webskill import context
    from webskill.infrastructure.web_corpus import store
    context.configure(tmp_path / "web-skill-state")
    monkeypatch.setattr(store, "_DB_PATH_OVERRIDE", str(tmp_path / "web-skill.sqlite3"))
