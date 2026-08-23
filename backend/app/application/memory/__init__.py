"""Memory application package.

Single entry point (the "one door") over Elira's two long-term memory engines —
see :mod:`app.application.memory.facade`. Prefer importing from here:

    from app.application.memory import recall, add_fact, search_semantic
"""

from app.application.memory.facade import (
    add_fact,
    add_semantic,
    authoritative_facts,
    default_profile,
    delete_fact,
    fact_context,
    fact_stats,
    list_facts,
    list_profiles,
    prune,
    recall,
    reflect_chat,
    search_facts,
    search_semantic,
    semantic_context,
)

__all__ = [
    "add_fact",
    "add_semantic",
    "authoritative_facts",
    "default_profile",
    "delete_fact",
    "fact_context",
    "fact_stats",
    "list_facts",
    "list_profiles",
    "prune",
    "recall",
    "reflect_chat",
    "search_facts",
    "search_semantic",
    "semantic_context",
]
