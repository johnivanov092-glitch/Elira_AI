"""Compatibility import for the single shared sqlite implementation."""
import sys
from elira_common import sqlite as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.sqlite import (
        LOGGER as LOGGER,
        DEFAULT_SQLITE_TIMEOUT_SECONDS as DEFAULT_SQLITE_TIMEOUT_SECONDS,
        DEFAULT_SQLITE_JOURNAL_MODE as DEFAULT_SQLITE_JOURNAL_MODE,
        _SUPPORTED_JOURNAL_MODES as _SUPPORTED_JOURNAL_MODES,
        connect_sqlite as connect_sqlite,
    )
