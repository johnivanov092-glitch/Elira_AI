"""Compatibility import for the single shared redaction implementation."""
import sys
from elira_common import redaction as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.redaction import (
        REDACTED as REDACTED,
        _SECRET_KEY_RE as _SECRET_KEY_RE,
        _INLINE_PATTERNS as _INLINE_PATTERNS,
        redact_text as redact_text,
        redact_secrets as redact_secrets,
    )
