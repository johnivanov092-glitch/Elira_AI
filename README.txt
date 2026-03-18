PHASE 17 — REAL DIFF + STRUCTURED HISTORY PACK

Что внутри:
- frontend/src/components/DiffViewer.jsx
- frontend/src/components/AutoCodingPanel.jsx
- frontend/src/components/CodeWorkspace.jsx
- frontend/src/api/ide.js
- backend/app/api/routes/jarvis_run_history.py
- backend/app/main.py.patch.txt

Что дает:
- нормальный diff preview
- apply только после preview
- rollback по run_id
- structured run history
- trace/events для UI

Важно:
- визуал агента не трогает
- Tauri не трогает
- это safe pack для встраивания в текущий shell
