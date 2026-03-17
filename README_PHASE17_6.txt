JARVIS PHASE 17.6 PATCH

Что внутри:
- backend/app/api/routes/jarvis_patch.py
- frontend/src/api/ide.js
- frontend/src/components/FileExplorer.jsx
- frontend/src/components/BatchVerifyPanel.jsx
- frontend/src/components/CodeWorkspace.jsx
- frontend/src/styles.css

Что добавляет:
- staging нескольких файлов в File Explorer
- batch apply:
  POST /api/jarvis/patch/apply-batch
- batch verify:
  POST /api/jarvis/patch/verify-batch
- панель Batch Verify
- подготовка под multi-file patch workflow

Важно:
- staged файлы применяются пакетно с текущими staged contents
- batch apply не делает транзакционный rollback всех файлов разом, а применяет их последовательно
- backend нужно перезапустить после установки
