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

# Single cap for all file-upload routes (voice / pdf / files / library). An
# unbounded await file.read() risks OOM on a huge upload; routes reject bigger
# bodies with HTTP 413. 25 MiB mirrors chat.py's attachment limit.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

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
