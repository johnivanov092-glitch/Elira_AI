"""Secret redaction for audit / approval surfaces.

Scrubs secret-looking VALUES from tool args and free-text reasons before they
are persisted (ApprovalStore ``args_json``) or emitted to the event bus /
Telegram. Redaction is **value-level**: it keeps the command/structure visible
(so a human can still see WHAT they are approving) and replaces only the
sensitive value with ``[REDACTED]``.

Important: this never touches the canonical args digest used for approval
matching (``args_sha256`` is computed from the RAW args), so redacting the
displayed ``args_json`` does NOT break approval matching on retry.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Dict keys whose VALUE is always treated as a secret.
_SECRET_KEY_RE = re.compile(
    r"(?i)(pass(?:word|wd)?|pwd|secret|token|api[-_ ]?key|apikey|authorization|"
    r"auth[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|private[-_ ]?key|"
    r"credential|cookie|session[-_ ]?id)"
)

# Inline secret patterns inside free text / shell commands. Order matters:
# Bearer first so the token is masked before the generic key:value rule runs.
_INLINE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer <token>  /  Bearer <token>
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1" + REDACTED),
    # CLI flags: --password X / --token=X / --api-key:X
    (
        re.compile(r"(?i)(--?(?:password|passwd|pwd|token|api[-_]?key|secret|auth)[\s=:]+)\S+"),
        r"\1" + REDACTED,
    ),
    # key=value / key: value (env / json-ish), key is secret-named.
    (
        re.compile(
            r"(?i)\b((?:password|passwd|pwd|secret|token|api[-_]?key|apikey|"
            r"authorization|access[-_]?token|refresh[-_]?token)[\"']?\s*[:=]\s*[\"']?)"
            r"[^\s\"',;}&|]+"
        ),
        r"\1" + REDACTED,
    ),
]


def redact_text(value: str) -> str:
    """Mask secret substrings in a free-text string (command / reason / header)."""
    out = value
    for pattern, repl in _INLINE_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_secrets(value: Any) -> Any:
    """Recursively redact secrets from args (dict/list/str), preserving shape.

    - dict: a secret-named key has its value fully replaced; other values recurse.
    - str: secret substrings are masked, structure kept visible.
    - list/tuple: each item recurses. Other scalars pass through unchanged.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = REDACTED
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value
