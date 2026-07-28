import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_tauri_csp_allows_only_loopback_live_preview_frames():
    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    csp = config["app"]["security"]["csp"]
    frame_src = next(part.strip() for part in csp.split(";")
                     if part.strip().startswith("frame-src "))

    assert "http://localhost:*" in frame_src
    assert "http://127.0.0.1:*" in frame_src
    assert "https://localhost:*" in frame_src
    assert "https://127.0.0.1:*" in frame_src
    assert "http:" not in frame_src.split()
    assert "https:" not in frame_src.split()
