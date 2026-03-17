JARVIS PHASE 17.5 PATCH

Что внутри:
- backend/app/api/routes/jarvis_patch.py
- frontend/src/api/ide.js
- frontend/src/components/DiffViewer.jsx
- frontend/src/components/PatchHistoryPanel.jsx
- frontend/src/components/TerminalPanel.jsx
- frontend/src/components/CodeWorkspace.jsx
- frontend/src/styles.css

Что добавляет:
- diff по строкам через backend /api/jarvis/patch/diff
- история патчей в SQLite:
  - GET /api/jarvis/patch/history/list
  - GET /api/jarvis/patch/history/get
- лог действий apply / rollback в patch_history
- просмотр unified diff
- панель истории патчей в Code tab
- verify теперь тоже возвращает diff и статистику

Важно:
- 17.5 меняет jarvis_patch.py, поэтому backend нужно перезапустить
- история патчей пишется в data/jarvis_state.db, таблица patch_history
- multi-file apply здесь не добавлен, но история и diff уже готовы под него
