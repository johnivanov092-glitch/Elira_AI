# Stage 3 — React + Vite workspace

Что добавлено:
- React + Vite frontend
- Sidebar
- Chat
- Agent Panel
- Подключение к backend:
  - `/api/models`
  - `/api/profiles`
  - `/api/settings`
  - `/api/chat/send`

## Как запустить
1. Backend должен уже работать на `http://127.0.0.1:8000`
2. Запустить:
   `D:\AIWork\jarvis_work\scripts\run_frontend.bat`
3. Открыть:
   `http://127.0.0.1:5173`

## Что уже умеет
- Загружает модели и профили
- Показывает defaults из backend
- Отправляет сообщения в `/api/chat/send`
- Динамически открывает правую Agent Panel
