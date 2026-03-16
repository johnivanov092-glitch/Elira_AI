# Что за что отвечает

## backend/app/core/config.py
Базовые пути и константы. Теперь хранит данные в `data/`, а не рядом с Python-модулями.

## backend/app/core/memory.py
SQLite-память, профили, knowledge base, семантический и keyword поиск.
Убрана зависимость от `streamlit.cache_resource` и `st.session_state`.

## backend/app/core/files.py
Работа с файлами, чатами, Project Analyzer. Теперь функции возвращают словари состояния,
которые удобно отдавать через API.

## backend/app/core/llm.py
Вызовы Ollama, budget_contexts, retry на ctx overflow, streaming. Теперь история чата и warnings
передаются параметрами, а не через Streamlit UI.

## backend/app/core/agents.py
Агентные сценарии: multi-agent, planner, browser, build loop, terminal, images.
На этом шаге убран доступ к `st.session_state` в `run_multi_agent`.

## backend/app/services/*
Тонкий слой orchestration между core и API.

## backend/app/api/routes/*
FastAPI-роуты для проверки backend и первых сценариев.
