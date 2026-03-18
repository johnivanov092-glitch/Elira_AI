PHASE 16 — AUTO-CODING LOOP STARTER PACK

Куда класть:

1) frontend/src/components/AutoCodingPanel.jsx
2) frontend/src/api/ide.js   -> заменить или аккуратно слить методы
3) backend/app/api/routes/jarvis_autocode.py
4) backend/app/main.py       -> подключить router
5) frontend/src/components/CodeWorkspace.jsx -> заменить если хочешь встроить кнопку Auto-Fix

Что дает:
- запрос AI patch proposal
- preview suggestions
- apply selected patch
- verify run
- bounded loop (max_steps)
- безопасный цикл без auto-commit

Важно:
- визуал агента не трогает
- Tauri не трогает
- Git auto-push не делает
- commit не делает
