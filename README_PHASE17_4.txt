JARVIS PHASE 17.4 PATCH

Что внутри:
- backend/app/main.py
- backend/app/api/routes/jarvis_patch.py
- frontend/src/api/ide.js
- frontend/src/components/CodeWorkspace.jsx
- frontend/src/components/TerminalPanel.jsx
- frontend/src/styles.css

Что добавляет:
- реальный apply patch на диск:
  POST /api/jarvis/patch/apply
- реальный rollback из backup:
  POST /api/jarvis/patch/rollback
- verify проверки:
  POST /api/jarvis/patch/verify
- backup файлов в data/patch_backups
- UI кнопки:
  - Apply Patch
  - Rollback
  - Verify

Важно:
- apply работает только по существующим файлам
- rollback сработает только если для файла уже есть backup после apply
- backup создаётся автоматически перед apply

Как ставить:
1. Скопируй файлы поверх проекта с заменой.
2. Перезапусти backend.
3. Перезапусти frontend / tauri.
