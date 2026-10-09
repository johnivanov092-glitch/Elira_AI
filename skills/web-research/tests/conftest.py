import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(autouse=True)
def isolated_skill(tmp_path, monkeypatch):
    from webskill import context
    from webskill.infrastructure.web_corpus import store
    context.configure(tmp_path)
    monkeypatch.setattr(store, '_DB_PATH_OVERRIDE', str(tmp_path/'web.sqlite3'))
    monkeypatch.setenv('LOCAL_EMBED_ENABLED', 'false')
