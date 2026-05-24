from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(".").resolve()
UPLOAD_ROOT = PROJECT_ROOT / "data" / "chat_uploads_tmp"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
TMP_UPLOAD_TTL_SECONDS = 24 * 60 * 60

EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "tmp",
    "temp",
    "logs",
    ".elira_chat_uploads",
    "data/chat_uploads_tmp",
}
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".rs", ".css", ".scss", ".html", ".htm",
    ".sql", ".sh", ".bat", ".ps1", ".ini", ".cfg", ".conf", ".env", ".example",
    ".xml", ".csv",
}
TEXT_NAMES = {"Dockerfile", "Makefile", ".gitignore"}
MAX_READ_BYTES = 512 * 1024
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_AGENT_FILE_BYTES = 256 * 1024

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))

CHAT_SESSIONS: dict[str, dict[str, Any]] = {}
ATTACHMENT_INDEX: dict[str, dict[str, Any]] = {}

