# Telegram

- send: {"chat_id":123,"text":"Сообщение","parse_mode":"Markdown"}.
- messages: {"chat_id":123,"limit":20}.

Токен разрешает существующее хранилище Elira. Не передавай его в аргументах,
не записывай в SKILL.md и не выводи. Отправка только по поручению пользователя.
Входящий бот и настройки UI используют runtime.py из этого же навыка.

Штатный запуск: `python <папка-навыка>/telegram.py send --input args.json --output result.json`
или сценарий messages с его JSON. Отдельного инструмента telegram нет; токен берёт
runtime из хранилища. Настройка бота — в UI (Настройки → Telegram).
