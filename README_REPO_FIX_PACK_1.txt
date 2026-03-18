JARVIS REPO FIX PACK 1

Это уже не абстрактный патч, а прямой repo-fix под текущий main.

Что заменяет:
- frontend/src/App.jsx
- frontend/src/api/ide.js
- frontend/src/components/JarvisChatShell.jsx
- frontend/src/components/IdeWorkspaceShell.jsx
- frontend/src/components/FileExplorerPanel.jsx
- frontend/src/components/TerminalPanel.jsx
- frontend/src/styles.css

Что чинит:
- убирает лишнюю букву J сверху
- делает верхний бар адаптивным
- убирает строку 'Режим: chat • qwen3:8b'
- даёт рабочие методы listChats / createChat / getMessages / addMessage / execute
- чинит Новый чат
- подтягивает несколько моделей через fallback
- добавляет выбор контекста 4k–256k
- уменьшает овал ввода примерно на 25%
- разводит Code layout чтобы элементы не налезали друг на друга

Важно:
- chat API пока с fallback в localStorage, если backend не даёт chat routes
- это быстрый рабочий repo-fix, чтобы ты смог начать пользоваться интерфейсом
