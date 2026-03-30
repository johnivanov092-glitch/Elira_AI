"""
plugin_system.py — система плагинов Elira AI v2.

Плагины = .py файлы в data/plugins/
Каждый плагин должен иметь: def run(args: dict) -> dict

Расширенный API плагинов:
  - TRIGGERS: list[str]  — фразы автоматического вызова (AI сама решит)
  - HOOKS: dict          — хуки: on_start, on_message, on_response, on_error
  - CONFIG: dict         — настройки по умолчанию (пользователь может менять)
  - CATEGORY: str        — категория: "utility", "integration", "analysis", etc.
  - ICON: str            — эмодзи иконка

Lifecycle:
  on_start()          — вызывается при загрузке плагина
  on_message(text)    — вызывается на каждое сообщение пользователя (до AI)
  on_response(text)   — вызывается после ответа AI
  run(args)           — основная функция (вызов пользователем или авто-триггер)
"""
from __future__ import annotations
import importlib.util
import json
import logging
import sys
from typing import Any

from app.core.config import DATA_DIR

logger = logging.getLogger(__name__)

PLUGINS_DIR = DATA_DIR / "plugins"
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_FILE = DATA_DIR / "plugins_config.json"

_plugins: dict[str, dict] = {}
_plugin_states: dict[str, bool] = {}  # name → enabled/disabled


# ═══════════════════════════════════════════════════════════════
# КОНФИГ (включение/выключение, пользовательские настройки)
# ═══════════════════════════════════════════════════════════════

def _load_config() -> dict:
    """Загружает конфиг плагинов из JSON."""
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Plugin config load error: {e}")
    return {}


def _save_config(config: dict):
    """Сохраняет конфиг плагинов."""
    try:
        _CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Plugin config save error: {e}")


def _get_plugin_config(name: str) -> dict:
    """Конфиг конкретного плагина."""
    config = _load_config()
    return config.get(name, {})


def _set_plugin_config(name: str, data: dict):
    """Обновляет конфиг плагина."""
    config = _load_config()
    config[name] = {**config.get(name, {}), **data}
    _save_config(config)


# ═══════════════════════════════════════════════════════════════
# ЗАГРУЗКА / ПЕРЕЗАГРУЗКА
# ═══════════════════════════════════════════════════════════════

def load_plugins() -> dict:
    """Загружает все плагины из data/plugins/."""
    global _plugins, _plugin_states
    _plugins = {}
    loaded = []
    errors = []
    config = _load_config()

    for py_file in sorted(PLUGINS_DIR.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"elira_plugin_{name}", py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not hasattr(mod, "run") or not callable(mod.run):
                errors.append({"name": name, "error": "Нет функции run(args)"})
                continue

            # Извлекаем метаданные
            triggers = getattr(mod, "TRIGGERS", [])
            hooks = getattr(mod, "HOOKS", {})
            default_config = getattr(mod, "CONFIG", {})
            category = getattr(mod, "CATEGORY", "utility")
            icon = getattr(mod, "ICON", "🔌")

            # Состояние включения (из конфига, по умолчанию — включён)
            plugin_conf = config.get(name, {})
            enabled = plugin_conf.get("enabled", True)
            user_settings = plugin_conf.get("settings", {})

            _plugins[name] = {
                "module": mod,
                "path": str(py_file),
                "description": getattr(mod, "DESCRIPTION", ""),
                "author": getattr(mod, "AUTHOR", ""),
                "version": getattr(mod, "VERSION", "1.0"),
                "category": category,
                "icon": icon,
                "triggers": triggers if isinstance(triggers, list) else [],
                "hooks": hooks if isinstance(hooks, dict) else {},
                "default_config": default_config,
                "user_settings": {**default_config, **user_settings},
                "enabled": enabled,
            }
            _plugin_states[name] = enabled
            loaded.append(name)

            # Вызываем on_start хук если есть
            if enabled and hasattr(mod, "on_start") and callable(mod.on_start):
                try:
                    mod.on_start()
                except Exception as e:
                    logger.warning(f"Plugin {name} on_start error: {e}")

        except Exception as e:
            errors.append({"name": name, "error": str(e)})

    return {"ok": True, "loaded": loaded, "errors": errors, "count": len(loaded)}


def reload_plugins() -> dict:
    """Перезагружает все плагины."""
    for name in list(sys.modules.keys()):
        if name.startswith("elira_plugin_"):
            del sys.modules[name]
    return load_plugins()


# ═══════════════════════════════════════════════════════════════
# СПИСОК / ИНФО
# ═══════════════════════════════════════════════════════════════

def list_plugins() -> dict:
    """Список всех плагинов с метаданными."""
    items = []
    for name, info in _plugins.items():
        items.append({
            "name": name,
            "description": info["description"],
            "author": info["author"],
            "version": info["version"],
            "category": info["category"],
            "icon": info["icon"],
            "enabled": info["enabled"],
            "triggers": info["triggers"],
            "has_hooks": bool(info["hooks"]) or any(
                hasattr(info["module"], h) for h in ("on_message", "on_response", "on_start")
            ),
            "config": info["user_settings"],
            "path": info["path"],
        })
    return {"ok": True, "plugins": items, "count": len(items)}


def get_plugin_info(name: str) -> dict:
    """Подробная информация о плагине."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    info = _plugins[name]
    return {
        "ok": True,
        "name": name,
        "description": info["description"],
        "author": info["author"],
        "version": info["version"],
        "category": info["category"],
        "icon": info["icon"],
        "enabled": info["enabled"],
        "triggers": info["triggers"],
        "hooks": list(info["hooks"].keys()) + [
            h for h in ("on_start", "on_message", "on_response")
            if hasattr(info["module"], h)
        ],
        "config": info["user_settings"],
        "default_config": info["default_config"],
        "path": info["path"],
    }


# ═══════════════════════════════════════════════════════════════
# ВКЛЮЧЕНИЕ / ВЫКЛЮЧЕНИЕ
# ═══════════════════════════════════════════════════════════════

def enable_plugin(name: str) -> dict:
    """Включает плагин."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    _plugins[name]["enabled"] = True
    _plugin_states[name] = True
    _set_plugin_config(name, {"enabled": True})
    return {"ok": True, "name": name, "enabled": True}


def disable_plugin(name: str) -> dict:
    """Выключает плагин."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    _plugins[name]["enabled"] = False
    _plugin_states[name] = False
    _set_plugin_config(name, {"enabled": False})
    return {"ok": True, "name": name, "enabled": False}


def update_plugin_settings(name: str, settings: dict) -> dict:
    """Обновляет пользовательские настройки плагина."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}"}
    merged = {**_plugins[name]["user_settings"], **settings}
    _plugins[name]["user_settings"] = merged
    _set_plugin_config(name, {"settings": merged})
    return {"ok": True, "name": name, "settings": merged}


# ═══════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════

def run_plugin(name: str, args: dict = None) -> dict:
    """Запускает плагин по имени."""
    if name not in _plugins:
        return {"ok": False, "error": f"Плагин не найден: {name}. Доступные: {list(_plugins.keys())}"}

    info = _plugins[name]
    if not info["enabled"]:
        return {"ok": False, "error": f"Плагин {name} выключен"}

    try:
        # Добавляем user_settings в аргументы
        full_args = {**info["user_settings"], **(args or {})}
        result = info["module"].run(full_args)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        return result
    except Exception as e:
        return {"ok": False, "error": f"Ошибка плагина {name}: {e}"}


# ═══════════════════════════════════════════════════════════════
# ХУКИ — вызываются агентом автоматически
# ═══════════════════════════════════════════════════════════════

def fire_hook(hook_name: str, data: Any = None) -> list[dict]:
    """Вызывает хук во всех активных плагинах, которые его реализуют."""
    results = []
    for name, info in _plugins.items():
        if not info["enabled"]:
            continue
        mod = info["module"]
        fn = getattr(mod, hook_name, None)
        if fn and callable(fn):
            try:
                result = fn(data)
                if result:
                    results.append({"plugin": name, "result": result})
            except Exception as e:
                logger.warning(f"Plugin {name} hook {hook_name} error: {e}")
    return results


def check_triggers(user_text: str) -> list[dict]:
    """Проверяет триггеры всех активных плагинов. Возвращает совпавшие."""
    matched = []
    lower = user_text.lower()
    for name, info in _plugins.items():
        if not info["enabled"]:
            continue
        for trigger in info.get("triggers", []):
            if trigger.lower() in lower:
                matched.append({"name": name, "trigger": trigger, "info": info})
                break
    return matched


def run_triggered(user_text: str) -> list[dict]:
    """Находит и запускает плагины по триггерам. Возвращает результаты."""
    results = []
    for match in check_triggers(user_text):
        name = match["name"]
        result = run_plugin(name, {"text": user_text, "trigger": match["trigger"]})
        results.append({"plugin": name, "trigger": match["trigger"], **result})
    return results


# ═══════════════════════════════════════════════════════════════
# АВТОЗАГРУЗКА
# ═══════════════════════════════════════════════════════════════

load_plugins()

# Создаём примеры если папка пустая
_EXAMPLE = PLUGINS_DIR / "example_hello.py"
if not _EXAMPLE.exists() and not list(PLUGINS_DIR.glob("*.py")):
    _EXAMPLE.write_text('''"""Пример плагина Elira AI."""
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
''', encoding="utf-8")
    load_plugins()
