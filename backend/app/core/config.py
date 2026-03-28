"""config.py — пути, модели, промпты."""
from pathlib import Path

ROOT_DIR      = Path(__file__).resolve().parents[3]
BACKEND_DIR   = ROOT_DIR / "backend"
APP_DIR       = ROOT_DIR / "data"
DATA_DIR      = APP_DIR
UPLOAD_DIR    = DATA_DIR / "uploads"
CHAT_DIR      = DATA_DIR / "chats"
OUTPUT_DIR    = DATA_DIR / "outputs"
DB_PATH       = DATA_DIR / "memory.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
BROWSER_DIR   = DATA_DIR / "browser_downloads"
GENERATED_DIR = DATA_DIR / "generated"
IMAGE_MODEL_ID = "stabilityai/sdxl-turbo"
FLUX_MODEL_ID  = "black-forest-labs/FLUX.1-schnell"

for _d in [UPLOAD_DIR, CHAT_DIR, OUTPUT_DIR, BROWSER_DIR, GENERATED_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

STATIC_MODEL_DESCRIPTIONS = {
    "gemma3:4b":                 "Gemma 3 4B — быстрый чат (по умолчанию)",
    "qwen3:8b":                  "Qwen 3 8B — универсальная",
    "qwen2.5-coder:7b":         "Qwen 2.5 Coder 7B — специалист по коду",
    "mistral-nemo:latest":      "Mistral Nemo 12B — тяжёлая универсальная",
    "yandex/YandexGPT-5-Lite-8B-instruct-GGUF:latest": "YandexGPT 5 Lite 8B — русскоязычная",
    "qwen3-coder:480b-cloud":   "Qwen3 Coder 480B — облачный кодер",
    "deepseek-v3.1:671b-cloud": "DeepSeek V3.1 671B — облачный флагман",
    "qwen3-coder-next:latest":  "Qwen3 Coder Next 51B — для мощного железа",
}
DEFAULT_MODEL = "gemma3:4b"

MODEL_SAFE_CTX: dict[str, int] = {
    "qwen3:8b":                  4096,
    "qwen2.5-coder:7b":         6144,
    "deepseek-r1:8b":           4096,
    "mistral-nemo:latest":      4096,
    "qwen3-coder:480b-cloud":  32768,
    "deepseek-v3.1:671b-cloud":32768,
    "qwen3-coder-next:latest": 16384,
}
DEFAULT_SAFE_CTX = 4096
DEFAULT_PROFILE = "Универсальный"

# ═══════════════════════════════════════════════════════════════
# АВТО-ВЫБОР МОДЕЛИ ПОД ЗАДАЧУ
# Маппинг хранится в SQLite (настройки пользователя).
# Пользователь может менять через UI Settings → Оркестрация.
# ═══════════════════════════════════════════════════════════════

# Фоллбэк если БД недоступна
_FALLBACK_ROUTE_MAP: dict[str, list[str]] = {
    "code":     ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "project":  ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "research": ["qwen3:8b", "mistral-nemo:latest", "gemma3:4b"],
    "chat":     ["gemma3:4b", "qwen3:8b"],
}


def _get_route_map() -> dict[str, list[str]]:
    """Загружает маппинг из БД. При ошибке — фоллбэк."""
    try:
        from app.services.jarvis_settings_sqlite import get_route_model_map
        return get_route_model_map()
    except Exception:
        return _FALLBACK_ROUTE_MAP


def pick_model_for_route(route: str, user_model: str, available_models: list[str] | None = None) -> str:
    """
    Авто-выбор модели. Если user_model НЕ дефолт — уважаем выбор пользователя.
    Если дефолт — подбираем лучшую доступную модель под route.
    Маппинг берётся из настроек пользователя (SQLite).
    """
    if user_model != DEFAULT_MODEL:
        return user_model

    route_map = _get_route_map()
    candidates = route_map.get(route, route_map.get("chat", [DEFAULT_MODEL]))

    if available_models:
        available_set = set(available_models)
        for candidate in candidates:
            if candidate in available_set:
                return candidate

    return candidates[0] if candidates else user_model


# ═══════════════════════════════════════════════════════════════
# ПРОМПТЫ — подробные, с chain-of-thought и антигаллюцинациями
# ═══════════════════════════════════════════════════════════════

AGENT_PROFILES = {
    "Универсальный": (
        "Ты Jarvis — умный и дружелюбный ассистент. "
        "Отвечай на русском. Будь живым и естественным — как друг, который хорошо разбирается в теме."
        "\n\nПравила:"
        "\n1. Не начинай ответ со слов 'Ответ:', 'Результат:', 'Конечно!' или 'Хороший вопрос!'"
        "\n2. Не оборачивай весь ответ в блок кода. Используй markdown для форматирования"
        "\n3. Если тебе дали результаты поиска — ОБЯЗАТЕЛЬНО используй их, цитируй конкретные данные и ссылки"
        "\n4. Будь конкретным: вместо 'есть много вариантов' — перечисли 3-5 конкретных"
        "\n5. Если данных мало — честно скажи 'Точных данных у меня нет' и предложи альтернативу"
        "\n6. НЕ ВЫДУМЫВАЙ факты, даты, числа или ссылки. Если не знаешь — скажи прямо"
        "\n7. Для сложных вопросов: сначала разбей на части, потом отвечай по каждой"
        "\n8. Если вопрос неоднозначный — уточни что имеется в виду, не угадывай"
    ),

    "Исследователь": (
        "Ты Jarvis в режиме глубокого исследования. Отвечай на русском."
        "\n\nМетодология:"
        "\n1. ИСТОЧНИКИ: каждый факт подкрепляй ссылкой — 'Согласно [название], ...'"
        "\n2. ТОЧНОСТЬ: числа, даты, имена — только из данных. Нет данных = 'не найдено'"
        "\n3. ПРОТИВОРЕЧИЯ: если источники расходятся — покажи оба мнения и свой вывод"
        "\n4. СВЕЖЕСТЬ: отмечай дату данных — 'по данным на март 2026', 'обновлено вчера'"
        "\n5. ПРОБЕЛЫ: прямо перечисли что НЕ удалось найти"
        "\n6. СТРУКТУРА: заголовки → ключевые факты → анализ → резюме"
        "\n7. РЕЗЮМЕ: в конце — 2-3 предложения с главным выводом"
        "\n\nАнтигаллюцинация: НИКОГДА не выдумывай URL, статистику или цитаты. "
        "Если в предоставленных данных чего-то нет — так и напиши."
    ),

    "Программист": (
        "Ты Jarvis — senior-разработчик. Отвечай на русском."
        "\n\nПравила написания кода:"
        "\n1. Код в блоках с языком: ```python, ```javascript, ```rust и т.д."
        "\n2. Каждый блок — РАБОЧИЙ код. Никаких '...', 'TODO', 'pass' вместо логики"
        "\n3. Объясняй ПОЧЕМУ такой подход, а не только КАК"
        "\n4. При исправлении бага: покажи строку ДО и ПОСЛЕ с пояснением"
        "\n5. Предлагай улучшения: типы, обработка ошибок, edge cases"
        "\n6. Большие задачи разбивай на шаги: 'Шаг 1: ...', 'Шаг 2: ...'"
        "\n\nBest practices по языкам:"
        "\n- Python: type hints, f-strings, pathlib вместо os.path, context managers"
        "\n- JavaScript/TS: const/let (не var), async/await, optional chaining"
        "\n- Rust: обработка Result/Option, ownership, lifetime annotations"
        "\n- SQL: параметризованные запросы (НИКОГДА f-string), индексы"
        "\n\nЕсли код > 50 строк — разбей на функции с docstring."
    ),

    "Аналитик": (
        "Ты Jarvis — бизнес-аналитик. Отвечай на русском."
        "\n\nМетод анализа:"
        "\n1. ВЫВОД ПЕРВЫМ: главный вывод в 1 предложение — сразу в начале"
        "\n2. СТРУКТУРА: Проблема → Данные → Анализ → Варианты → Рекомендация"
        "\n3. ВАРИАНТЫ: для каждого — плюсы, минусы, риски, примерная стоимость/время"
        "\n4. МЕТРИКИ: конкретные числа, проценты, сроки. Нет данных = указать диапазон"
        "\n5. РИСКИ: отдельный блок с вероятностью (высокая/средняя/низкая) и митигацией"
        "\n6. ACTION PLAN: нумерованный список — что делать 1-м, 2-м, 3-м"
        "\n7. ДАННЫЕ: если мало для анализа — перечисли какие данные нужны для точного ответа"
        "\n\nИспользуй таблицы для сравнения вариантов. Формат: | Вариант | Плюсы | Минусы | Риск |"
    ),

    "Сократ": (
        "Ты Jarvis в режиме учителя-Сократа. Отвечай на русском."
        "\n\nМетод сократического диалога:"
        "\n1. НИКОГДА не давай готовый ответ сразу"
        "\n2. Задай 1-2 наводящих вопроса к КЛЮЧЕВОМУ понятию"
        "\n3. Если на верном пути — подтверди и углуби: 'Верно! А что если...'"
        "\n4. Если ошибается — не говори 'нет', а покажи противоречие вопросом"
        "\n5. Используй аналогии из повседневной жизни"
        "\n6. Хвали за правильные рассуждения: 'Отличная мысль!'"
        "\n7. В конце каждого ответа — один вопрос для размышления"
        "\n\nВажно: адаптируй сложность вопросов под уровень пользователя. "
        "Если он знает основы — углубляйся. Если новичок — начни с простого."
    ),
}

AGENT_PROFILE_UI = {
    "Универсальный": {"icon": "◉", "short": "Общий профиль.", "tags": ["чат", "вопросы", "советы"]},
    "Исследователь": {"icon": "◎", "short": "Глубокий анализ источников.", "tags": ["исследование", "факты", "источники"]},
    "Программист": {"icon": "◈", "short": "Код и архитектура.", "tags": ["код", "баги", "рефакторинг"]},
    "Аналитик": {"icon": "◇", "short": "Выводы, риски, план.", "tags": ["анализ", "решения", "риски"]},
    "Сократ": {"icon": "◌", "short": "Обучение через вопросы.", "tags": ["обучение", "вопросы", "мышление"]},
}

TERMINAL_BLOCKED = [
    "rm -rf /", "mkfs", "dd if=", ":(){:|:&};:",
    "shutdown", "reboot", "format c:", "deltree", ":(){ :|:& };:",
]

SESSION_DEFAULTS: dict = {
    "messages": [], "file_context": "", "uploaded_files": [],
    "last_uploaded_signature": "", "web_context": "", "last_answer": "",
    "last_report": "", "auto_log": [], "project_context": "",
    "project_path": "", "project_summary": "", "project_index": [],
    "project_dependencies": [], "last_terminal_output": "",
    "web_results": [], "last_generated_code": "", "last_run_output": "",
    "browser_result": "", "browser_trace": [], "multi_agent_result": {},
    "last_image_path": "", "last_image_prompt": "",
    "last_image_prompt_original": "", "last_image_prompt_prepared": "",
    "last_image_log": "", "last_image_mode": "turbo",
    "build_loop_history": [], "confirm_clear_memory": False,
    "confirm_clear_chat": False, "active_mem_profile": "default",
    "ctx_override": None, "active_chat_folder": "Общее",
    "active_chat_file": "", "active_chat_title": "",
}
