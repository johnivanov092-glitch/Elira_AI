# Скрипты Elira

Команды ниже выполняются из корня репозитория. Пользовательские точки входа
остаются в корне: `Elira.bat`, `Elira_Mobile.bat`, `kill_elira.bat`.

| Запуск | Назначение |
| --- | --- |
| `scripts\run_tauri_dev.bat` | Backend и Tauri в режиме разработки |
| `scripts\run_backend.bat` | Отдельный backend для разработки |
| `scripts\run_frontend.bat` | Отдельный frontend для разработки |
| `scripts\build_exe.bat` | Сборка Tauri installer |
| `scripts\Elira_Autonomy_Test.bat` | Изолированная проверка автономности; [исходный протокол](../docs/archive/research/UNATTENDED_ADMIN_TRIAL_RU.md) |
| `backend\.venv\Scripts\python.exe scripts\elira_release.py --help` | CLI менеджера релизов |
| `backend\.venv\Scripts\python.exe scripts\foundation_client.py --help` | CLI Windows Foundation |

Полный порядок проверок — [AGENTS.md](../AGENTS.md); правила выпуска и
восстановления — [RELEASE_LIFECYCLE.md](../docs/RELEASE_LIFECYCLE.md).
Перенос BAT-файлов в эту папку сохраняет рабочий каталог корня проекта.
