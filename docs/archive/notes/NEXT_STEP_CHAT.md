# Следующий шаг: проверка POST /api/chat/send

## URL
`POST http://127.0.0.1:8000/api/chat/send`

## Пример запроса
```json
{
  "model_name": "qwen2.5-coder:7b",
  "profile_name": "Универсальный",
  "user_input": "Привет, кто ты?",
  "history": []
}
```

## Пример PowerShell
```powershell
$body = @{
  model_name   = "qwen2.5-coder:7b"
  profile_name = "Универсальный"
  user_input   = "Привет, кто ты?"
  history      = @()
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/chat/send" -Method Post -ContentType "application/json" -Body $body
```

## Что внутри работает
- `api/routes/chat.py` — HTTP endpoint
- `schemas/chat.py` — схема запроса/ответа
- `services/chat_service.py` — сервисная прослойка
- `core/llm.py` — реальный вызов Ollama
