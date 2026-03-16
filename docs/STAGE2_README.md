# Stage 2 patch

Добавляет:
- `GET /`
- `GET /api/models`
- `GET /api/profiles`
- `GET /api/settings`

## Как применить
Распаковать архив поверх `D:\AIWork\jarvis_work`.

## Как проверить
1. Перезапустить backend
2. Выполнить:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_stage2.ps1
```
