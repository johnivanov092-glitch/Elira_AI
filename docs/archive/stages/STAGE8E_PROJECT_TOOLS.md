# Stage 8E — Project File Tools

Что добавляет:
- backend/app/services/project_service.py
- tools:
  - list_project_tree
  - read_project_file
  - write_project_file
  - search_project
- agent auto-routing для запросов про проект / backend / frontend / файлы
- test script: scripts/test_stage8e_project_tools.ps1

Что это даёт:
- агент видит дерево проекта
- умеет искать по кодовой базе
- умеет читать и писать текстовые файлы проекта
- делает первый шаг к режиму локального coding agent
