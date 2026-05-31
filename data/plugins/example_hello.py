"""Пример плагина Elira AI."""
DESCRIPTION = "Приветствие — пример плагина"
AUTHOR = "Elira"
VERSION = "1.0"
ICON = "👋"
CATEGORY = "utility"
TRIGGERS = ["привет плагин", "hello plugin"]

# Настройки по умолчанию (пользователь может менять в UI)
CONFIG = {
    "greeting": "Привет",
    "emoji": True,
}

def on_start():
    """Вызывается при загрузке плагина."""
    pass

def on_message(text):
    """Вызывается на каждое сообщение пользователя."""
    return None  # Вернуть строку = добавить в контекст AI

def run(args: dict) -> dict:
    name = args.get("name", "мир")
    greeting = args.get("greeting", "Привет")
    emoji = "! 👋" if args.get("emoji") else "!"
    return {"ok": True, "message": f"{greeting}, {name}{emoji}"}
