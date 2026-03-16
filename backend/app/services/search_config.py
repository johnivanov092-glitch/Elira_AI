from __future__ import annotations

PREFERRED_DOC_DOMAINS = {
    "fastapi": ["fastapi.tiangolo.com"],
    "ollama": ["ollama.com", "github.com/ollama/ollama"],
    "react": ["react.dev"],
    "vite": ["vite.dev"],
    "python": ["docs.python.org"],
    "django": ["docs.djangoproject.com"],
    "flask": ["flask.palletsprojects.com"],
    "tauri": ["tauri.app", "v2.tauri.app"],
}

COMMUNITY_DOMAINS = {
    "stackoverflow.com",
    "reddit.com",
    "zhihu.com",
    "medium.com",
    "dev.to",
    "habr.com",
}

BAD_PATH_PARTS = {
    "/login",
    "/signup",
    "/search",
    "/account",
    "/pricing",
}

DOC_PATH_HINTS = (
    "docs",
    "documentation",
    "reference",
    "api",
    "tutorial",
    "guide",
    "learn",
    "quickstart",
    "install",
    "usage",
    "examples",
    "getting-started",
)

OFFICIAL_HINTS = (
    "official",
    "документац",
    "официаль",
    "docs",
    "documentation",
)
