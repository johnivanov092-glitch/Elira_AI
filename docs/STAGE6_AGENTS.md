# Stage 6 — Agents

Что добавлено:
- `POST /api/agents/run`
- agent service с timeline:
  - анализ запроса
  - поиск в памяти
  - проверка библиотеки
  - синтез ответа

Во frontend:
- активный раздел Agents
- запуск задачи агенту
- timeline шагов
- итоговый ответ агента
- meta блок

Как проверить:
1. Распаковать архив поверх `D:\AIWork\jarvis_work`
2. Перезапустить backend
3. Если нужно, перезапустить frontend
4. Выполнить:
   `powershell -ExecutionPolicy Bypass -File D:\AIWork\jarvis_work\scripts\test_stage6_agents.ps1`
5. Открыть вкладку Agents в UI
