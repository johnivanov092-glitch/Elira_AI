# CSV

`analyze.py csv --input args.json`: аргументы `file_path` (обязателен),
`filters`, `group_by`, `aggregate`, `query` (не исполняется как код).
Пример: {"file_path":"prices.csv","aggregate":[{"column":"price","fn":"sum"}]}.
Фильтр: {"column":"status","op":"==","value":"paid"}; поддержаны ==, !=, >, >=,
<, <=, contains, not_contains, in, empty, not_empty. group_by — список имён колонок.
Агрегат: {"fn":"sum","column":"amount"}; функции count, sum, avg, min, max,
count_distinct. Для count без колонки: {"fn":"count"}.

Полный пример: {"file_path":"orders.csv","filters":[{"column":"status","op":"==","value":"paid"}],"aggregate":[{"fn":"count"},{"fn":"sum","column":"amount"}]}.
Этого формата достаточно для штатного запуска; исходник table_query.py читай при
конкретной ошибке или изменении навыка. Пропуски skipped_non_numeric и ограничение
числа показанных групп (note) не скрывай. Для скачивания публикуй result.json через
resource_publish; этот файл содержит тот же JSON-результат, что stdout.
