"""Compatibility import for the single shared text implementation."""
import sys
from elira_common import text as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.text import (
        truncate_middle as truncate_middle,
        truncate_text as truncate_text,
        contains_any as contains_any,
    )
