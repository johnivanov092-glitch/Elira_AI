# CSV

`analyze.py csv --input args.json`: аргументы `file_path` (обязателен),
`filters`, `group_by`, `aggregate`, `query` (не исполняется как код).
Пример: {"file_path":"prices.csv","aggregate":[{"column":"price","fn":"sum"}]}.
Для точного формата агрегатов смотри table_query.py; ошибки строк не скрывай.
