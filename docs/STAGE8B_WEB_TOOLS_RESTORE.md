# Stage 8B — Web Tools Restore

Что восстановлено в jarvis_work:
- backend/app/services/web_service.py
- tools:
  - search_web
  - research_web
- agent auto-routing:
  - если запрос выглядит как web/search/doc query,
    агент запускает research_web и добавляет web context в ответ

Как проверить:
- scripts/test_stage8b_web_tools.ps1
