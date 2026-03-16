# Jarvis Work

Это стартовая миграция со Streamlit на FastAPI + React + Tauri.

Уже сделано:
- ядро разложено по backend/app/core
- `llm.py`, `files.py`, `memory.py` очищены от прямой зависимости на Streamlit
- `agents.py` больше не читает `st.session_state` в multi-agent сценарии
- добавлены базовые роуты: health, profiles, library, chat

Следующий этап:
1. Прогнать backend.
2. Проверить `/api/health` и `/api/chat/send`.
3. Дальше чистить `agents.py` глубже и добавлять WebSocket run events.
