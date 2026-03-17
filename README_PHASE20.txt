JARVIS PHASE 20 PATCH

Что внутри:
- backend/app/main.py
- backend/app/api/routes/jarvis_phase20.py
- frontend/src/api/ide.js
- frontend/src/components/Phase20Panel.jsx
- README_PHASE20.txt

Что добавляет:
- Autonomous Project Agent:
  POST /api/jarvis/phase20/run
- Phase 20 history:
  GET /api/jarvis/phase20/history/list
  GET /api/jarvis/phase20/history/get
- multi-agent reasoning:
  planner / coder / reviewer / tester / execution
- project-level execution plan

Что делает:
- строит reasoning по проекту и выбранным файлам
- готовит plan / operations / review / verify / execution flow
- подготавливает staged/apply/verify workflow для следующего шага

Важно:
- backend нужно перезапустить
- frontend панель Phase20Panel пока отдельная, для интеграции в CodeWorkspace нужен следующий UI patch
