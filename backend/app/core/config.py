"""config.py — пути, модели, промпты."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR      = Path(__file__).resolve().parents[3]
BACKEND_DIR   = ROOT_DIR / "backend"
# DATA_DIR honours ELIRA_DATA_DIR (falls back to <repo>/data) so tests and alt
# deployments never touch the live data/ tree — every derived path below inherits
# the redirect. conftest.py sets ELIRA_DATA_DIR to a temp dir before app modules
# import, so the whole suite writes there instead of the developer's real data/.
DATA_DIR      = Path(os.getenv("ELIRA_DATA_DIR") or (ROOT_DIR / "data")).resolve()
APP_DIR       = DATA_DIR
UPLOAD_DIR    = DATA_DIR / "uploads"
CHAT_DIR      = DATA_DIR / "chats"
OUTPUT_DIR    = DATA_DIR / "outputs"
SETTINGS_PATH = DATA_DIR / "settings.json"
BROWSER_DIR   = DATA_DIR / "browser_downloads"
GENERATED_DIR = DATA_DIR / "generated"

# Create the runtime dirs under the RESOLVED data dir (the test temp dir under
# pytest, the live data/ in production) — never the live tree during tests.
for _d in [UPLOAD_DIR, CHAT_DIR, OUTPUT_DIR, BROWSER_DIR, GENERATED_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

STATIC_MODEL_DESCRIPTIONS = {
    "local-model":                "Local llama-server - OpenAI-compatible endpoint",
    "qwen3-coder:480b-cloud":   "Qwen3 Coder 480B — облачный кодер",
    "deepseek-v3.1:671b-cloud": "DeepSeek V3.1 671B — облачный флагман",
    "qwen3-coder-next:latest":  "Qwen3 Coder Next 51B — для мощного железа",
}
DEFAULT_MODEL = "local-model"

MODEL_SAFE_CTX: dict[str, int] = {
    "local-model":              131072,
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
    "code":     ["local-model"],
    "project":  ["local-model"],
    "research": ["local-model"],
    "chat":     ["local-model"],
}


def _get_route_map() -> dict[str, list[str]]:
    """Загружает маппинг из БД. При ошибке — фоллбэк."""
    try:
        from app.application.elira_memory.settings import get_route_model_map
        return get_route_model_map()
    except Exception:
        return _FALLBACK_ROUTE_MAP


# Sentinel values that mean "route via orchestration table, don't trust
# user_model verbatim". Anything matching is auto-routed; everything else
# is treated as an explicit choice the user wants honoured.
AUTO_ROUTE_TOKENS = frozenset({"", "auto", "\u0430\u0432\u0442\u043e"})


def is_auto_route(user_model: str | None) -> bool:
    """True when the chat request asks for orchestration-based routing."""
    if user_model is None:
        return True
    return str(user_model).strip().lower() in AUTO_ROUTE_TOKENS


# ═══════════════════════════════════════════════════════════════
# P9.3 — Model Profiles в общем routing-path.
# Детерминированное сопоставление route → role. Роли совпадают с
# таблицей model_profiles (monitoring): fast / code / strong / embedding.
# Это ЕДИНСТВЕННЫЙ источник истины для ролей маршрутизации — вызывающий
# код не должен заводить второй маппинг.
# ═══════════════════════════════════════════════════════════════
ROUTE_TO_ROLE: dict[str, str] = {
    "chat":     "fast",
    "research": "strong",
    "code":     "code",
    "project":  "code",
}
DEFAULT_ROUTE_ROLE = "fast"


def route_to_role(route: str | None) -> str:
    """Сопоставить route планировщика с ролью model_profiles (детерминированно)."""
    return ROUTE_TO_ROLE.get(str(route or "").strip().lower(), DEFAULT_ROUTE_ROLE)


def _get_profile_for_role(role: str) -> dict | None:
    """Первый включённый профиль модели для *role*, или None при любой ошибке.

    Ленивый импорт держит app.core свободным от зависимости на app.application
    во время загрузки модуля (зеркалит _get_route_map)."""
    try:
        from app.application.monitoring.runtime import get_profile_for_role
        return get_profile_for_role(role)
    except Exception:
        return None


@dataclass(frozen=True)
class ModelRouteDecision:
    """Структурированный результат маршрутизации запроса к конкретной модели.

    Единый источник истины для решения о модели (P9.3). pick_model_for_route —
    back-compat обёртка, возвращающая только .model.
    """
    model: str
    route: str
    role: str
    requested_model: str
    source: str  # explicit | profile | route_map | default
    profile_id: str | None = None
    provider: str | None = None
    context_limit: int | None = None
    timeout_seconds: int | None = None
    fallback_reason: str | None = None
    cloud_consent_required: bool = False
    cloud_skipped: bool = False


def _safe_positive_int(value: object) -> int | None:
    try:
        ivalue = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return ivalue if ivalue > 0 else None


def resolve_model_for_route(
    route: str,
    requested_model: str | None,
    available_models: list[str] | None = None,
    *,
    cloud_consent: bool = False,
) -> ModelRouteDecision:
    """Единый resolver маршрутизации модели (P9.3). Порядок выбора:

      1. явный выбор пользователя — уважается дословно;
      2. включённый профиль для роли маршрута — если его модель доступна
         (cloud — только при cloud_consent, иначе пропуск);
      3. route_model_map — первый доступный кандидат;
      4. DEFAULT_MODEL.

    Недоступная/неизвестная модель профиля → ограниченный fallback на route_map
    (бесконечный retry не допускается). Не создаёт второй router — это и есть
    общий routing-path; pick_model_for_route делегирует сюда.
    """
    role = route_to_role(route)
    requested = "" if requested_model is None else str(requested_model)

    # 1. Explicit user choice wins (honoured verbatim).
    if not is_auto_route(requested_model):
        return ModelRouteDecision(
            model=requested, route=route, role=role,
            requested_model=requested, source="explicit",
        )

    fallback_reason: str | None = None
    cloud_required = False
    cloud_skipped = False

    # 2. Enabled profile for the route's role.
    profile = _get_profile_for_role(role)
    if not profile:
        fallback_reason = "no_profile_for_role"
    else:
        profile_model = str(profile.get("model") or "")
        cloud_required = bool(profile.get("cloud_consent_required"))
        if cloud_required and not cloud_consent:
            cloud_skipped = True
            fallback_reason = "cloud_consent_required"
        elif not profile_model:
            fallback_reason = "profile_model_empty"
        elif available_models is None:
            fallback_reason = "available_models_unknown"
        elif profile_model not in set(available_models):
            fallback_reason = "profile_model_unavailable"
        else:
            return ModelRouteDecision(
                model=profile_model, route=route, role=role,
                requested_model=requested, source="profile",
                profile_id=(str(profile.get("id") or "") or None),
                provider=(str(profile.get("provider") or "") or None),
                context_limit=_safe_positive_int(profile.get("context_limit")),
                timeout_seconds=_safe_positive_int(profile.get("timeout_seconds")),
                cloud_consent_required=cloud_required,
            )

    # 3. Existing orchestration route map.
    route_map = _get_route_map()
    candidates = route_map.get(route, route_map.get("chat", [DEFAULT_MODEL]))
    chosen: str | None = None
    if available_models:
        available_set = set(available_models)
        for candidate in candidates:
            if candidate in available_set:
                chosen = candidate
                break
    if chosen is None and candidates:
        chosen = candidates[0]

    if chosen:
        return ModelRouteDecision(
            model=chosen, route=route, role=role, requested_model=requested,
            source="route_map", fallback_reason=fallback_reason,
            cloud_consent_required=cloud_required, cloud_skipped=cloud_skipped,
        )

    # 4. Default.
    return ModelRouteDecision(
        model=(requested or DEFAULT_MODEL), route=route, role=role,
        requested_model=requested, source="default",
        fallback_reason=(fallback_reason or "empty_route_map"),
        cloud_consent_required=cloud_required, cloud_skipped=cloud_skipped,
    )


def effective_context_limit(
    requested_num_ctx: int,
    *,
    monitoring_max_context: int | None = None,
    profile_context_limit: int | None = None,
    model: str | None = None,
) -> int:
    """min(requested, monitoring cap, profile cap, model safe-ctx если известен).

    Неизвестная модель НЕ режется автоматически до DEFAULT_SAFE_CTX — model cap
    применяется только когда известен (MODEL_SAFE_CTX). Возвращает requested,
    если ни одного положительного ограничения нет. Никогда не увеличивает
    requested — только ограничивает (явный выбор не обходит лимиты).
    """
    caps: list[int] = []
    for value in (requested_num_ctx, monitoring_max_context, profile_context_limit):
        if isinstance(value, int) and value > 0:
            caps.append(value)
    model_cap = MODEL_SAFE_CTX.get(model) if model else None
    if isinstance(model_cap, int) and model_cap > 0:
        caps.append(model_cap)
    return min(caps) if caps else requested_num_ctx


def pick_model_for_route(route: str, user_model: str, available_models: list[str] | None = None) -> str:
    """Back-compat обёртка над resolve_model_for_route — возвращает только модель.

    Сохраняет прежний контракт (str) для существующих вызовов (agent_registry и
    т.д.); общий порядок маршрутизации и поведение полностью совпадают.
    """
    return resolve_model_for_route(route, user_model, available_models).model


# ═══════════════════════════════════════════════════════════════
# ПРОМПТЫ — подробные, с chain-of-thought и антигаллюцинациями
# ═══════════════════════════════════════════════════════════════

AGENT_PROFILES = {
    "Универсальный": (
        "Ты Elira — умная и дружелюбная AI-ассистентка. "
        "Отвечай на русском. Будь живой и естественной — как подруга, которая хорошо разбирается в теме."
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
        "Ты Elira в режиме глубокого исследования. Отвечай на русском. Твоя единственная "
        "задача — добывать и проверять факты, а не рассуждать 'вообще'."
        "\n\nЖёсткие правила (нарушение = провал ответа):"
        "\n1. ИСТОЧНИК НА КАЖДЫЙ ФАКТ. Формат: 'Согласно [источник], ...'. Факт без источника не пиши вовсе"
        "\n2. ТОЛЬКО ИЗ ДАННЫХ: числа, даты, имена, URL — строго из контекста/результатов поиска. Нечего процитировать = 'не найдено'"
        "\n3. НИКОГДА не выдумывай URL, статистику, цитаты, названия исследований. Лучше пустое поле, чем правдоподобная выдумка"
        "\n4. ПРОТИВОРЕЧИЯ: если источники расходятся — приведи обе позиции и свой вывод с пометкой уверенности (высокая/средняя/низкая)"
        "\n5. СВЕЖЕСТЬ: у каждого факта помечай дату данных — 'по данным на ...'"
        "\n6. ПРОБЕЛЫ: отдельным блоком 'Что не найдено' честно перечисли"
        "\n\nСтруктура ответа (строго):"
        "\n## Ключевые факты — список, каждый с источником"
        "\n## Анализ — что эти факты означают"
        "\n## Что не найдено — пробелы и противоречия"
        "\n## Вывод — 2-3 предложения, главное"
        "\n\nДисциплина: без воды и вводных фраз. Если данных в контексте нет вообще — ответ ровно один: "
        "'В предоставленных данных информации по запросу не найдено' + что нужно дать для ответа."
    ),

    "Программист": (
        "Ты Elira — senior-разработчица. Отвечай на русском. Пишешь только код, который реально работает."
        "\n\nАнтигаллюцинация (главное правило):"
        "\n- НЕ выдумывай API, имена методов, сигнатуры, флаги, названия пакетов. Не уверена в сигнатуре — так и скажи: 'нужно проверить в документации/исходниках'"
        "\n- Не выдавай несуществующую функцию за рабочую. Лучше честное 'не помню точную сигнатуру', чем правдоподобный фейк"
        "\n\nПравила написания кода:"
        "\n1. Код в блоках с языком: ```python, ```javascript, ```rust и т.д."
        "\n2. Каждый блок — РАБОЧИЙ код. Никаких '...', 'TODO', 'pass' вместо логики"
        "\n3. Обязательно покрой edge cases: пустой ввод, None/null, ошибки I/O, граничные значения"
        "\n4. При исправлении бага: покажи строку ДО и ПОСЛЕ с пояснением причины"
        "\n5. Большие задачи разбивай на шаги: 'Шаг 1: ...', 'Шаг 2: ...'"
        "\n\nЗапрещено в продакшен-коде: голый except/catch без типа, .unwrap() на внешних данных, "
        "any без причины, SQL через f-string, секреты в коде."
        "\n\nBest practices по языкам:"
        "\n- Python: type hints, f-strings, pathlib вместо os.path, context managers"
        "\n- JavaScript/TS: const/let (не var), async/await, optional chaining"
        "\n- Rust: обработка Result/Option, ownership, lifetime annotations"
        "\n- SQL: параметризованные запросы (НИКОГДА f-string), индексы"
        "\n\nСтруктура ответа (строго): кратко решение в 1-2 строках → код → почему так → риски/как проверить. "
        "Без длинных вступлений. Код > 50 строк — разбей на функции с docstring."
    ),

    "Аналитик": (
        "Ты Elira — бизнес-аналитик. Отвечай на русском. Доводишь до решения, а не до общих слов."
        "\n\nМетод анализа:"
        "\n1. РЕКОМЕНДАЦИЯ ПЕРВОЙ СТРОКОЙ: начни ответ с конкретного 'Рекомендую: ...'. Не размытое 'зависит'"
        "\n2. СТРУКТУРА: Рекомендация → Проблема → Данные → Варианты → Риски → План"
        "\n3. ВАРИАНТЫ: сравнивай таблицей, не прозой"
        "\n4. МЕТРИКИ ТОЛЬКО ИЗ ДАННЫХ: числа/проценты/сроки бери из контекста. Прикидка = диапазон + пометка '(оценка)'. Не выдумывай точных цифр"
        "\n5. РИСКИ: отдельный блок с вероятностью (высокая/средняя/низкая) и митигацией"
        "\n6. ПЛАН: нумерованный список — что делать 1-м, 2-м, 3-м"
        "\n7. ЧЕГО НЕ ХВАТАЕТ: обязательный блок — какие данные нужны, чтобы вывод был точнее"
        "\n\nТаблица сравнения (строго этот формат): | Вариант | Плюсы | Минусы | Риск | Оценка |"
        "\n\nДисциплина: без воды. 'Зависит от...' допустимо только если тут же даёшь развилку: 'если A — то X, если B — то Y'."
    ),

    "Сократ": (
        "Ты Elira в режиме учителя-Сократа. Отвечай на русском. Твоя цель — чтобы человек ПОНЯЛ и дошёл до результата, а не застрял."
        "\n\nМетод сократического диалога:"
        "\n1. Не вываливай готовый ответ сразу — веди вопросами к КЛЮЧЕВОМУ понятию (1-2 вопроса за раз, не больше)"
        "\n2. Если на верном пути — подтверди и углуби: 'Верно! А что если...'"
        "\n3. Если ошибается — не говори 'нет', а покажи противоречие вопросом"
        "\n4. Используй аналогии из повседневной жизни; хвали верные рассуждения"
        "\n5. Проверяй понимание на каждом шаге, прежде чем идти дальше"
        "\n6. ФАКТЫ ВСЕГДА ТОЧНЫЕ: формулы, даты, определения давай верно и не выдумывай. Учитель не врёт — если сам не уверен, скажи прямо"
        "\n7. ТУПИК: если человек застрял после 2-3 попыток — дай маленькую подсказку (следующий шаг), а не весь ответ. Главное — не бросить его без результата"
        "\n\nФормат: короткие шаги, один вопрос в конце. Адаптируй сложность под уровень: "
        "знает основы — углубляйся; новичок — начни с простого."
    ),
}

AGENT_PROFILE_UI = {
    "Универсальный": {"icon": "◉", "short": "Общий профиль.", "tags": ["чат", "вопросы", "советы"]},
    "Исследователь": {"icon": "◎", "short": "Глубокий анализ источников.", "tags": ["исследование", "факты", "источники"]},
    "Программист": {"icon": "◈", "short": "Код и архитектура.", "tags": ["код", "баги", "рефакторинг"]},
    "Аналитик": {"icon": "◇", "short": "Выводы, риски, план.", "tags": ["анализ", "решения", "риски"]},
    "Сократ": {"icon": "◌", "short": "Обучение через вопросы.", "tags": ["обучение", "вопросы", "мышление"]},
}

# Single source of truth for blocked terminal commands. Used by both the
# /api/terminal/exec runtime and domain/tools/terminal_tool.py. This is a
# best-effort guard against *accidental* destructive commands by the local
# user — it is NOT the security boundary (substring matching is bypassable).
# The real boundary is app.core.auth: non-local callers need a token.
TERMINAL_BLOCKED = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){:|:&};:",
    "shutdown", "reboot", "format c:", "deltree", ":(){ :|:& };:",
    "remove-item -recurse", "del /s", "rd /s", "rmdir /s",
    "git reset --hard", "git clean -fd", "git checkout --",
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
