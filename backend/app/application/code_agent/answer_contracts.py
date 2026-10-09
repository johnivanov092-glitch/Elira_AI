"""Compatibility import for the single shared quote_rules implementation."""
import sys
from elira_common import quote_rules as _implementation
sys.modules[__name__] = _implementation

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from elira_common.quote_rules import (
        _FENCED_CODE as _FENCED_CODE,
        _INLINE_CODE as _INLINE_CODE,
        _PAIRED_QUOTE as _PAIRED_QUOTE,
        _LIMIT as _LIMIT,
        _LABEL as _LABEL,
        _BLOCK as _BLOCK,
        _WORD as _WORD,
        _NEGATED_DIRECTIVE as _NEGATED_DIRECTIVE,
        QuoteWordLimitViolation as QuoteWordLimitViolation,
        infer_quote_word_limit as infer_quote_word_limit,
        _label_match as _label_match,
        _claimed_words as _claimed_words,
        _quote_spans as _quote_spans,
        _quote_word_count as _quote_word_count,
        _QUOTE_REQUEST as _QUOTE_REQUEST,
        _NAME_MAX_WORDS as _NAME_MAX_WORDS,
        explicit_quote_request as explicit_quote_request,
        is_name_like_quote as is_name_like_quote,
        normalize_quote_word_counts as normalize_quote_word_counts,
        quote_word_limit_violations as quote_word_limit_violations,
        quote_word_limit_correction as quote_word_limit_correction,
    )
