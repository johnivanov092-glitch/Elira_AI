from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "tests" / "smokes" / "memory_eval.py"


def test_memory_eval_runs_in_isolated_process_and_writes_reports(tmp_path: Path) -> None:
    output_dir = tmp_path / "report"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(output_dir)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["passed"] == report["total"] == 6
    assert all(case["status"] == "PASS" for case in report["cases"].values())
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "isolated temporary ELIRA_DATA_DIR" in markdown
    assert "encrypted_backup_restore" in markdown
