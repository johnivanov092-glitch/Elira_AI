# Stage 5 — Memory

Что добавлено:
- `GET /api/memory/profiles`
- `GET /api/memory/items/{profile}`
- `POST /api/memory/add`
- `POST /api/memory/search`
- `DELETE /api/memory/items/{profile}/{item_id}`
- `GET /api/memory/context/{profile}`

Во frontend:
- активный раздел Memory
- ручное добавление памяти
- список записей по профилю
- поиск по памяти
- удаление записи

Также chat теперь отправляется с `use_memory: true`,
а backend автоматически подмешивает найденную память в запрос к модели.

## Как проверить
1. Распаковать архив поверх `D:\AIWork\jarvis_work`
2. Перезапустить backend
3. При необходимости перезапустить frontend
4. Выполнить:
   `powershell -ExecutionPolicy Bypass -File D:\AIWork\jarvis_work\scripts\test_stage5_memory.ps1`
