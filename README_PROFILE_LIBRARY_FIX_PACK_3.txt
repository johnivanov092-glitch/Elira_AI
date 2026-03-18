JARVIS PROFILE + LIBRARY FIX PACK 3

Что заменяет:
- frontend/src/api/ide.js
- frontend/src/components/JarvisChatShell.jsx
- frontend/src/styles.css
- backend/app/main.py

Что исправляет:
- тёмный фон у select/options вместо белого
- реальные профили из backend /api/profiles + fallback
- модели Ollama читаются из payload.models, а не только 1-я
- профиль передаётся в /api/chat/send как profile_name
- Code использует /api/project-brain/snapshot и /api/project-brain/file
- библиотека получает явную кнопку загрузки файлов
- drag and drop остаётся
- в backend монтируются profiles и agents routes
