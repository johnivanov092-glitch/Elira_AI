"""Layer C — machine cross-check of the model's final claim against the
runtime's own record of file changes.

A local model reports by memory-of-intent, not by observed result: when an
edit_file/write_file fails it has no post-write state and tends to fabricate a
tidy "nothing changed" narrative. The journal, however, records every touched
path on the actual tool_call event, so we can catch the contradiction and
correct it deterministically — no model in the loop.

This is a leaf module: it imports nothing from agent_loop. agent_loop re-exports
these names so existing importers (tests, the loop) keep resolving them from
agent_loop unchanged.
"""
from __future__ import annotations

_NO_CHANGE_CLAIM_MARKERS = (
    "никаких изменений",
    "изменений нет",
    "изменения не вносил",
    "изменения не внесены",
    "ничего не изменил",
    "ничего не менял",
    "не вносил изменени",
    "не внёс изменени",
    "не внес изменени",
    "no changes were made",
    "no changes have been made",
    "i did not make any changes",
    "i have not made any changes",
    "nothing was changed",
    "no files were changed",
    "no files were modified",
)


def _claims_no_changes(text: str) -> bool:
    """True if the final text asserts that nothing in the project was changed."""
    low = (text or "").lower()
    return any(marker in low for marker in _NO_CHANGE_CLAIM_MARKERS)


def _layer_c_correction(final_text: str, changed_files: list[str]) -> str:
    """A correction note when the model claims 'no changes' but files were touched.

    Returns "" when the claim is consistent with the record (no correction needed).
    """
    if not changed_files:
        return ""
    if not _claims_no_changes(final_text):
        return ""
    listed = "\n".join(f"- {p}" for p in changed_files[:50])
    extra = "" if len(changed_files) <= 50 else f"\n…и ещё {len(changed_files) - 50}"
    return (
        "\n\n⚠️ Проверка по журналу выполнения: в ходе этого запуска изменения в "
        f"файлах всё-таки были ({len(changed_files)} шт.), хотя в ответе сказано "
        "обратное. Фактически затронутые файлы:\n"
        f"{listed}{extra}"
    )
