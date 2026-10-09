from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tool_schemas import build_tool_schemas
from app.application.tool_providers.builtin import BuiltinToolProvider
from app.application.tool_registry.runtime import get_tool, seed_builtin_tools


STAGE1_TOOLS = {
    "csv": ("auto", False, ["fs.read"]),
}


def test_unify_core_stage1_schemas_are_native() -> None:
    names = {schema["function"]["name"] for schema in build_tool_schemas()}
    assert set(STAGE1_TOOLS).issubset(names)


def test_unify_core_stage1_toolspecs_are_registered() -> None:
    seed_builtin_tools()
    for name, (_permission, side_effect, _scopes) in STAGE1_TOOLS.items():
        hit = get_tool(name)
        assert hit is not None
        assert hit["side_effect"] is side_effect


def test_unify_core_stage1_builtin_provider_dispatch_smoke(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("name,value\na,1\nb,2\n", encoding="utf-8")
    (tmp_path / "data.json").write_text(
        json.dumps([{"name": "a", "value": 1}], ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "fake.png").write_bytes(b"png")
    (tmp_path / "fake.docx").write_bytes(b"docx")

    provider = BuiltinToolProvider(tmp_path)
    checks: list[tuple[str, dict]] = [
        ("csv", provider.dispatch("csv", {"file_path": "data.csv"})),
    ]
    for name, result in checks:
        text = str(result.get("text") or "")
        assert text, name
        assert "ERROR:" not in text, (name, text)


def test_retired_generator_has_no_schema_dispatch_or_builtin_inventory(tmp_path):
    from app.application.tool_registry.builtins import build_builtin_tools
    names = {item["function"]["name"] for item in build_tool_schemas()}
    assert "file_gen" not in names
    assert "file_gen" not in {item["name"] for item in build_builtin_tools()}
    provider = BuiltinToolProvider(tmp_path)
    assert provider.owns("file_gen") is False
    assert provider.dispatch("file_gen", {"format": "word"})["error"] == "unknown_tool"
