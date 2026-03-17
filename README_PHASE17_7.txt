JARVIS PHASE 17.7 PATCH

Что внутри:
- backend/app/main.py
- backend/app/api/routes/jarvis_devtools.py
- frontend/src/api/ide.js
- frontend/src/components/ProjectMapPanel.jsx
- frontend/src/components/PatchPlanPanel.jsx
- frontend/src/components/FileOpsPanel.jsx
- frontend/src/components/CodeWorkspace.jsx
- frontend/src/styles.css

Что добавляет:
- Project Map:
  GET /api/jarvis/project/map
- File Ops:
  POST /api/jarvis/fs/create
  POST /api/jarvis/fs/delete
  POST /api/jarvis/fs/rename
- Patch Plan:
  POST /api/jarvis/patch/plan
- новые панели справа в Code Workspace:
  - Patch Plan
  - Project Map
  - File Ops

Важно:
- backend нужно перезапустить
- fs/delete удаляет только файлы, не директории
- project map ограничен по скану и не обходит blocked зоны
- patch plan пока rule-based, но уже готов как безопасная основа для автономного dev-агента
