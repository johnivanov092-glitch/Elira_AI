# Stage 4 — Settings + Library

Что добавлено:
- `POST /api/settings`
- `GET /api/library/files`
- `POST /api/library/activate`
- `DELETE /api/library/files/{filename}`

Во frontend:
- активный раздел Settings
- активный раздел Library
- сохранение model/profile по умолчанию
- просмотр файлов из `data/uploads`
- переключение active/inactive
- удаление файла

## Как проверить backend
`powershell -ExecutionPolicy Bypass -File D:\AIWork\jarvis_work\scripts\test_stage4.ps1`

## Как проверить frontend
1. Backend запущен
2. Frontend запущен
3. Перейти в Settings и нажать Сохранить
4. Перейти в Library
5. Положить файлы в `D:\AIWork\jarvis_work\data\uploads`
6. Нажать «Обновить»
