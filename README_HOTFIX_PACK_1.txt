JARVIS HOTFIX PACK 1

Что фиксит по твоему списку:
1. убирает лишнюю букву J сверху
2. делает верхний бар читаемым и адаптивным при изменении размера окна
3. добавляет динамическую загрузку списка моделей Ollama + fallback
4. добавляет chat API shim, чтобы убрать ошибку api.listChats is not a function
5. даёт отдельную hotfix chat/page/layout структуру без наложения элементов
6. убирает строку 'Режим: chat • qwen3:8b'
7. добавляет в настройки выбор контекста 4k–256k
8. чинит Новый чат через createChat fallback в localStorage
9. уменьшает панель ввода примерно на 25%

Что внутри:
- frontend/src/api/ide.js
- frontend/src/components/AppHeader.jsx
- frontend/src/components/ChatInputHotfix.jsx
- frontend/src/components/SettingsPanelHotfix.jsx
- frontend/src/components/ChatPageHotfix.jsx
- README_HOTFIX_PACK_1.txt

Важно:
- это быстрый hotfix pack под твои текущие симптомы
- его надо встроить в текущий экран/роут чата
- если у тебя главный экран уже рендерится через другой компонент, замени его на ChatPageHotfix временно
