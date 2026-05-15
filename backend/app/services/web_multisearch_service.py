"""Thin facade — all web multisearch logic lives in infrastructure/search/web_multisearch_service.py."""
from app.infrastructure.search.web_multisearch_service import (  # noqa: F401
    WebMultiSearchService,
    deep_search,
    fetch_page,
    multi_search,
    news_search,
)
