# Stage 8D — Python Executor

Что добавляет:
- backend/app/services/python_runner.py
- tool: python_execute
- agent auto-routing для запросов с python/вычислениями/кодом
- test script: scripts/test_stage8d_python_executor.ps1

Что умеет:
- безопасно выполнить небольшой Python-код
- вернуть stdout / stderr / locals
- использовать результат как context для ответа агента
