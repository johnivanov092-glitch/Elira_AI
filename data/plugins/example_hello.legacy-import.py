"""Пример плагина Jarvis."""
DESCRIPTION = "Приветствие — пример плагина"
AUTHOR = "Jarvis"
VERSION = "1.0"

def run(args: dict) -> dict:
    name = args.get("name", "мир")
    return {"ok": True, "message": f"Привет, {name}!"}
