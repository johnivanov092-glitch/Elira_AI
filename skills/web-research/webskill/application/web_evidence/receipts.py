"""Compatibility import for the single shared source_receipts implementation."""
import sys
from elira_common import source_receipts as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.source_receipts import (
        TIERS as TIERS,
        SOURCE_PATTERN as SOURCE_PATTERN,
        MAX_SOURCES as MAX_SOURCES,
        EXCERPT_CHARS as EXCERPT_CHARS,
        _STATUSES as _STATUSES,
        _source_dates as _source_dates,
        digest as digest,
        source_ids as source_ids,
        _identity as _identity,
        make_source as make_source,
        valid_source as valid_source,
        merge_sources as merge_sources,
        format_source as format_source,
        excerpt_sources as excerpt_sources,
    )
