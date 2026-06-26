"""Layer C — machine cross-check of the model's 'no changes' claim against the
run journal's record of touched files. The model (local Qwen) reports by
memory-of-intent, not observed result, so this deterministic check is the
load-bearing anti-lying layer. These tests pin the pure helpers.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import (  # noqa: E402
    _claims_no_changes,
    _layer_c_correction,
)


def test_claims_no_changes_detects_russian_and_english_markers() -> None:
    assert _claims_no_changes("В итоге никаких изменений в проекте нет.")
    assert _claims_no_changes("Я ничего не изменил, всё осталось как было.")
    assert _claims_no_changes("No changes were made to the codebase.")
    assert _claims_no_changes("I did not make any changes.")


def test_claims_no_changes_is_case_insensitive() -> None:
    assert _claims_no_changes("NIKAKIH... НИКАКИХ ИЗМЕНЕНИЙ нет")
    assert _claims_no_changes("NO CHANGES WERE MADE")


def test_claims_no_changes_false_when_reporting_real_work() -> None:
    assert not _claims_no_changes("Добавил функцию в utils.py и обновил тесты.")
    assert not _claims_no_changes("Edited 3 files and re-ran the suite.")
    assert not _claims_no_changes("")


def test_no_correction_when_no_files_touched() -> None:
    # Claim is truthful: journal is empty, so the claim must be left alone.
    assert _layer_c_correction("Никаких изменений нет.", []) == ""


def test_no_correction_when_claim_consistent_with_changes() -> None:
    # Files were touched AND the model reported work — no contradiction to flag.
    assert _layer_c_correction("Обновил app/main.py.", ["app/main.py"]) == ""


def test_correction_fires_on_contradiction_and_lists_files() -> None:
    changed = ["app/foo.py", "app/bar.py"]
    note = _layer_c_correction("Никаких изменений в проекте нет.", changed)
    assert note != ""
    assert "Проверка по журналу выполнения" in note
    assert "(2 шт.)" in note
    for path in changed:
        assert f"- {path}" in note


def test_correction_truncates_long_file_lists_to_50() -> None:
    changed = [f"app/f{i}.py" for i in range(60)]
    note = _layer_c_correction("nothing was changed", changed)
    assert "(60 шт.)" in note
    assert "- app/f0.py" in note
    assert "- app/f49.py" in note
    assert "- app/f50.py" not in note  # beyond the 50-line cap
    assert "…и ещё 10" in note
