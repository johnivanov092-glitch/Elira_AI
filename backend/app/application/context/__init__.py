"""Context management: compaction, budget tracking, memory, and policy."""

from app.application.context.compaction import maybe_compact
from app.application.context.profile import get_active_context_profile
from app.application.context.usage import (
    calculate_budget,
    check_context_limit,
    get_context_usage,
)

__all__ = [
    "calculate_budget",
    "check_context_limit",
    "get_active_context_profile",
    "get_context_usage",
    "maybe_compact",
]
