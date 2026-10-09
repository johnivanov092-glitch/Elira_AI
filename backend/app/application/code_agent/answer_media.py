"""Compatibility import for the single shared answer_media implementation."""
import sys
from elira_common import answer_media as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.answer_media import (
        MAX_ANSWER_MEDIA as MAX_ANSWER_MEDIA,
        _safe_remote_url as _safe_remote_url,
        _source_label as _source_label,
        image_media_from_search_results as image_media_from_search_results,
        merge_answer_media as merge_answer_media,
    )
