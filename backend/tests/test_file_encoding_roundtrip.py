"""File tools preserve valid UTF-8 before trying heuristic legacy codecs."""
from __future__ import annotations

import pytest

from app.application.code_agent.tools import _files


@pytest.mark.parametrize("text", ["Кедр", "цвет: синий", "маркер: RUNE-M9T2\nпроект: Кедр\nкоробок: 43\nцвет: синий\n"])
def test_write_then_read_cyrillic_utf8(tmp_path, text):
    result = _files.tool_write_file(tmp_path, path="card.txt", content=text)
    assert result["ok"]
    before = (tmp_path / "card.txt").read_bytes()
    read = _files.tool_read_file(tmp_path, path="card.txt")
    assert read["ok"]
    for line in text.splitlines():
        assert line in read["text"]
    assert (tmp_path / "card.txt").read_bytes() == before


@pytest.mark.parametrize("strict", [False, True])
def test_valid_utf8_never_reaches_heuristic_detector(monkeypatch, strict):
    def unexpected_detector(raw):
        pytest.fail("Valid UTF-8 must not depend on heuristic detection")
    monkeypatch.setattr(_files, "_HAS_CN", True)
    monkeypatch.setattr(_files, "_cn_from_bytes", unexpected_detector)
    assert _files._detect_encoding("Кедр".encode("utf-8"), strict=strict) == ("utf-8", False)


@pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "cp1251"])
def test_read_and_edit_preserve_existing_encoding(tmp_path, codec):
    text = "проект: Кедр\nцвет: синий\n"
    raw = text.encode(codec)
    target = tmp_path / "legacy.txt"
    target.write_bytes(raw)
    read = _files.tool_read_file(tmp_path, path="legacy.txt")
    assert read["ok"] and "проект: Кедр" in read["text"]
    assert target.read_bytes() == raw
    edited = _files.tool_edit_file(tmp_path, path="legacy.txt", old_string="Кедр", new_string="Сосна")
    assert edited["ok"]
    write_codec = "utf-8" if codec == "utf-8-sig" else codec
    assert target.read_bytes() == text.replace("Кедр", "Сосна").encode(write_codec)
