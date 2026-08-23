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
    "translator": ("auto", False, []),
    "regex": ("auto", False, []),
    "csv": ("auto", False, ["fs.read"]),
    "converter": ("auto", False, ["fs.read", "fs.write"]),
    "http_api": ("require_approval", True, ["net.outbound"]),
    "sql": ("require_approval", True, ["fs.read", "fs.write"]),
    "encrypt": ("require_approval", True, []),
    "archiver": ("require_approval", True, ["fs.read", "fs.write"]),
    "webhook": ("require_approval", True, []),
    "screenshot": ("require_approval", True, ["net.outbound", "fs.write"]),
    "file_gen": ("require_approval", True, ["fs.write"]),
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
        ("regex", provider.dispatch("regex", {"pattern": "a+", "text": "aa bb"})),
        ("csv", provider.dispatch("csv", {"file_path": "data.csv"})),
        ("converter", provider.dispatch("converter", {"source_path": "data.json", "target_format": "csv"})),
        ("sql", provider.dispatch("sql", {"action": "list"})),
        ("encrypt", provider.dispatch("encrypt", {"action": "encrypt", "text": "secret"})),
        ("archiver", provider.dispatch("archiver", {"action": "create", "source_path": "note.txt"})),
        ("webhook", provider.dispatch("webhook", {"action": "store", "data": {"ok": True}})),
    ]
    with patch(
        "app.application.skills_extra.runtime.translate_text",
        return_value={"ok": True, "translated": "hello"},
    ):
        checks.append(("translator", provider.dispatch("translator", {"text": "privet"})))
    with patch(
        "app.application.skills.runtime.http_request",
        return_value={"ok": True, "status": 200, "body": "ok"},
    ):
        checks.append(("http_api", provider.dispatch("http_api", {"url": "https://example.com"})))
    with patch(
        "app.application.skills.runtime.screenshot_url",
        return_value={"ok": True, "filename": "shot.png", "view_url": "/view"},
    ):
        checks.append(("screenshot", provider.dispatch("screenshot", {"url": "https://example.com"})))
    with patch(
        "app.application.skills.generate_word",
        return_value={
            "ok": True,
            "filename": "fake.docx",
            "path": str(tmp_path / "fake.docx"),
            "size": 4,
            "download_url": "/download",
        },
    ):
        checks.append(("file_gen", provider.dispatch("file_gen", {"format": "word", "content": "hello"})))

    for name, result in checks:
        text = str(result.get("text") or "")
        assert text, name
        assert "ERROR:" not in text, (name, text)
