# Что я поправил

- `backend/app/core/files.py`
  - убрал запись в `st.session_state` из `process_uploaded_files`
  - теперь функция возвращает обычный словарь состояния:
    - `uploaded_files`
    - `file_context`
    - `last_uploaded_signature`
  - добавил `list_uploaded_files(profile_name)`

- `backend/app/core/llm.py`
  - уже был переведён на `history=` и `warning_callback=`
  - больше не зависит от Streamlit UI

- `backend/app/core/memory.py`
  - уже был переведён на обычный модульный кэш `_EMBEDDER` и `_FAISS_CACHE`

- `backend/app/core/agents.py`
  - уже использует `project_context` и `file_context` через аргументы, а не из Streamlit

- `scripts/run_backend.bat`
  - исправлен запуск из правильной папки `backend`
  - убраны битые символы в путях
  - добавлена активация `.venv\Scripts\activate.bat`

- `backend/requirements.txt`
  - обновлён стартовый набор зависимостей

## Что проверить после распаковки

1. Запусти `scripts\run_backend.bat`
2. Открой `http://127.0.0.1:8000/api/health`
3. Должен прийти ответ `{"status":"ok"}`

## Что ещё не делал

- не строил полноценные endpoints для agent-runs и websocket streaming
- не переделывал frontend под live panel агента
- не переносил старые данные `memory.db / uploads / chats` автоматически
