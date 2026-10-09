"""Compatibility import for the single shared numbers implementation."""
import sys
from elira_common import numbers as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.numbers import (
        _CURRENCY_TOKENS as _CURRENCY_TOKENS,
        parse_decimal as parse_decimal,
        money as money,
        plain as plain,
    )
