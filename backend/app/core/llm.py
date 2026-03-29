"""
llm.py — вся работа с Ollama.

Ключевые фичи:
  • get_safe_ctx()     — безопасный num_ctx под RTX 4060 Ti 8 GB
  • budget_contexts()  — жёсткая обрезка контекстов под реальный лимит (ГЛАВНЫЙ ФИХ)
  • ask_model()        — обычный вызов с авто-retry при ошибке контекста
  • ask_model_stream() — стриминг с тем же retry
"""
import re
import warnings
from typing import Generator, List, Dict, Optional

# Ollama SDK использует httpx внутри и иногда не закрывает сокеты при стриминге.
# Это безопасный варнинг (GC подберёт), подавляем чтобы не засорять лог.
warnings.filterwarnings("ignore", category=ResourceWarning, module="httpx")
warnings.filterwarnings("ignore", category=ResourceWarning, module="bs4")

import ollama

from functools import lru_cache

from .config import MODEL_SAFE_CTX, DEFAULT_SAFE_CTX
from .persona_defaults import DEFAULT_PROFILE, PROFILE_MODE_OVERLAYS
from app.services.persona_service import build_persona_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# ТОКЕНЫ И ЛИМИТЫ
# ═══════════════════════════════════════════════════════════════════════════════

_CTX_ERROR_KEYWORDS = (
    "context length", "kv cache", "out of memory", "oom",
    "token limit", "exceeds", "too long", "failed to allocate",
    "requires more", "context size",
)


def _is_ctx_error(exc: Exception) -> bool:
    return any(kw in str(exc).lower() for kw in _CTX_ERROR_KEYWORDS)


def estimate_tokens(text: str) -> int:
    """~4 символа = 1 токен (быстрая оценка)."""
    return max(1, len(text) // 4)


def get_safe_ctx(model_name: str, requested_ctx: Optional[int] = None) -> int:
    """Возвращает аппаратно-безопасный num_ctx для модели."""
    hw_limit = MODEL_SAFE_CTX.get(model_name, DEFAULT_SAFE_CTX)
    if requested_ctx is None:
        return hw_limit
    return min(requested_ctx, hw_limit)


def _trim_history(messages: List[Dict], keep: int = 4) -> List[Dict]:
    """Оставляет только последние `keep` пар user/assistant."""
    return messages[-(keep * 2):] if len(messages) > keep * 2 else messages


# ═══════════════════════════════════════════════════════════════════════════════
# БЮДЖЕТИРОВАНИЕ КОНТЕКСТА  ← главный фикс
# ═══════════════════════════════════════════════════════════════════════════════

# Сколько токенов резервируем под базовые части (не контексты)
_RESERVE_PROFILE   = 120   # системный промпт профиля
_RESERVE_INSTR     = 60    # инструкция "используй данные..."
_RESERVE_USER      = 300   # сообщение пользователя (запас)
_RESERVE_HISTORY   = 400   # история чата (запас на пару сообщений)
_RESERVE_RESPONSE  = 512   # место для ответа модели
_RESERVE_TOTAL     = (_RESERVE_PROFILE + _RESERVE_INSTR +
                      _RESERVE_USER + _RESERVE_HISTORY + _RESERVE_RESPONSE)

# Приоритет и доля бюджета для каждого типа контекста
# (чем выше приоритет — тем больше токенов получит)
_CTX_BUDGET_SHARES = {
    "memory":  0.30,   # память — самое ценное
    "file":    0.30,   # загруженные файлы
    "web":     0.25,   # веб результаты
    "project": 0.15,   # проект (обычно большой — отдаём меньше)
}


def budget_contexts(
    num_ctx: int,
    file_context: str = "",
    project_context: str = "",
    web_context: str = "",
    memory_context: str = "",
    user_input: str = "",
    history: Optional[List[Dict]] = None,
) -> Dict[str, str]:
    """
    Обрезает каждый контекст так, чтобы суммарно всё влезло в num_ctx.
    Возвращает dict с обрезанными строками.

    Логика:
      1. Считаем сколько токенов уже занято (история + сообщение пользователя + резерв)
      2. Остаток — бюджет для контекстов
      3. Распределяем по долям, обрезаем каждый
    """
    # Токены истории (реальные)
    history_tokens = sum(
        estimate_tokens(m.get("content", "")) for m in (history or [])
    )
    user_tokens = estimate_tokens(user_input)

    # Доступный бюджет для всех контекстов
    used_fixed = (
        _RESERVE_PROFILE + _RESERVE_INSTR +
        history_tokens + user_tokens +
        _RESERVE_RESPONSE
    )
    available = max(0, num_ctx - used_fixed)

    if available <= 0:
        # Совсем нет места — отдаём пустые строки
        return {"file": "", "project": "", "web": "", "memory": ""}

    def _trim(text: str, max_tokens: int) -> str:
        if not text.strip():
            return ""
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n\n[...обрезано под лимит контекста...]"

    result = {}
    for key, share in _CTX_BUDGET_SHARES.items():
        budget_tokens = int(available * share)
        raw = {
            "memory":  memory_context,
            "file":    file_context,
            "web":     web_context,
            "project": project_context,
        }[key]
        result[key] = _trim(raw, budget_tokens)

    return result


def context_size_warning(
    num_ctx: int,
    file_context: str = "",
    project_context: str = "",
    web_context: str = "",
    memory_context: str = "",
    user_input: str = "",
    history: Optional[List[Dict]] = None,
) -> Optional[str]:
    """
    Считает реальное заполнение и возвращает предупреждение если > 85%.
    Теперь НЕ показывает 1085% — потому что budget_contexts уже обрезал данные.
    """
    history_tokens = sum(
        estimate_tokens(m.get("content", "")) for m in (history or [])
    )
    total = (
        estimate_tokens(file_context) +
        estimate_tokens(project_context) +
        estimate_tokens(web_context) +
        estimate_tokens(memory_context) +
        estimate_tokens(user_input) +
        history_tokens +
        _RESERVE_PROFILE + _RESERVE_INSTR + _RESERVE_RESPONSE
    )
    pct = total / num_ctx
    if pct > 0.85:
        return (
            f"⚠️ Контекст заполнен на ~{int(pct*100)}% ({total}/{num_ctx} токенов). "
            f"Данные автоматически обрезаны под лимит."
        )
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

def build_system_prompt(
    profile_name: str,
    file_context: str,
    project_context: str,
    web_context: str,
    memory_context: str,
    use_web: bool,
    use_memory: bool,
) -> str:
    """Собирает system prompt. Контексты уже обрезаны в budget_contexts()."""
    normalized_profile = profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    parts = [
        build_persona_prompt(normalized_profile),
        "Используй только релевантные данные. Если данных не хватает — скажи прямо. "
        "Если пользователь просит код — давай рабочий код и объясняй изменения.",
    ]
    if file_context.strip():
        parts.append(f"Контекст из загруженных файлов:\n{file_context}")
    if project_context.strip():
        parts.append(f"Контекст из папки проекта:\n{project_context}")
    if use_web and web_context.strip():
        parts.append(f"Контекст из интернета:\n{web_context}")
    if use_memory and memory_context.strip():
        parts.append(f"Контекст из памяти:\n{memory_context}")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# ASK MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def ask_model(
    model_name: str,
    profile_name: str,
    user_input: str,
    file_context: str = "",
    project_context: str = "",
    web_context: str = "",
    memory_context: str = "",
    use_web: bool = False,
    use_memory: bool = False,
    temp: float = 0.2,
    include_history: bool = True,
    num_ctx: int = 4096,
    history: Optional[List[Dict]] = None,
    warning_callback=None,
) -> str:
    profile_name = profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    safe_ctx = get_safe_ctx(model_name, num_ctx)
    history  = list(history or []) if include_history else []

    def _call(ctx: int, hist: List[Dict],
              fc: str, pc: str, wc: str, mc: str) -> str:
        # Бюджетируем контексты под реальный ctx
        budgeted = budget_contexts(ctx, fc, pc, wc, mc, user_input, hist)
        system = build_system_prompt(
            profile_name,
            budgeted["file"], budgeted["project"],
            budgeted["web"],  budgeted["memory"],
            use_web, use_memory,
        )
        msgs = [{"role": "system", "content": system}]
        msgs.extend(hist)
        msgs.append({"role": "user", "content": user_input})
        resp = ollama.chat(
            model=model_name,
            messages=msgs,
            options={"temperature": temp, "num_ctx": ctx, "num_thread": 8},
        )
        return resp["message"]["content"]

    # Попытка 1 — нормальный вызов
    try:
        return _call(safe_ctx, history,
                     file_context, project_context, web_context, memory_context)
    except Exception as e:
        if not _is_ctx_error(e):
            raise

    # Попытка 2 — обрезаем историю + уменьшаем ctx вдвое
    trimmed_hist = _trim_history(history)
    reduced_ctx  = max(512, safe_ctx // 2)
    try:
        result = _call(reduced_ctx, trimmed_hist,
                       file_context, project_context, web_context, memory_context)
        if warning_callback:
            warning_callback(
                f"⚠️ Авто-retry: ctx уменьшен до {reduced_ctx}, "
                f"история обрезана до {len(trimmed_hist)//2} пар."
            )
        return result
    except Exception as e:
        if not _is_ctx_error(e):
            raise

    # Попытка 3 — аварийный режим: только профиль + вопрос, ctx=512
    try:
        msgs_min = [
            {"role": "system", "content": build_persona_prompt(profile_name, model_name=model_name)},
            {"role": "user",   "content": user_input},
        ]
        resp = ollama.chat(
            model=model_name,
            messages=msgs_min,
            options={"temperature": temp, "num_ctx": 512, "num_thread": 8},
        )
        if warning_callback:
            warning_callback(
                "⚠️ Аварийный режим: ctx=512, без истории и контекста. "
                "Очистите чат и уменьшите загруженные файлы."
            )
        return resp["message"]["content"]
    except Exception as final_err:
        raise RuntimeError(
            "Модель не смогла ответить даже при минимальном контексте. "
            f"Попробуйте другую модель или очистите чат.\nОшибка: {final_err}"
        ) from final_err


# ═══════════════════════════════════════════════════════════════════════════════
# ASK MODEL STREAM
# ═══════════════════════════════════════════════════════════════════════════════

def ask_model_stream(
    model_name: str,
    profile_name: str,
    user_input: str,
    file_context: str = "",
    project_context: str = "",
    web_context: str = "",
    memory_context: str = "",
    use_web: bool = False,
    use_memory: bool = False,
    temp: float = 0.2,
    num_ctx: int = 4096,
    history: Optional[List[Dict]] = None,
    warning_callback=None,
) -> Generator[str, None, None]:
    """Генератор токенов для st.write_stream(). При ctx-ошибке откатывается к ask_model."""
    profile_name = profile_name if profile_name in PROFILE_MODE_OVERLAYS else DEFAULT_PROFILE
    safe_ctx = get_safe_ctx(model_name, num_ctx)
    history  = list(history or [])

    # Бюджетируем ДО стриминга
    budgeted = budget_contexts(safe_ctx, file_context, project_context,
                               web_context, memory_context, user_input, history)

    # Показываем предупреждение по ОБРЕЗАННЫМ данным (не оригинальным)
    warn = context_size_warning(
        safe_ctx, budgeted["file"], budgeted["project"],
        budgeted["web"], budgeted["memory"], user_input, history,
    )
    if warn and warning_callback:
        warning_callback(warn)

    system = build_system_prompt(
        profile_name,
        budgeted["file"], budgeted["project"],
        budgeted["web"],  budgeted["memory"],
        use_web, use_memory,
    )
    msgs = [{"role": "system", "content": system}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_input})

    try:
        stream = ollama.chat(
            model=model_name, messages=msgs, stream=True,
            options={"temperature": temp, "num_ctx": safe_ctx, "num_thread": 8},
        )
        for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token
    except Exception as e:
        if _is_ctx_error(e):
            result = ask_model(
                model_name=model_name, profile_name=profile_name,
                user_input=user_input,
                file_context=file_context, project_context=project_context,
                web_context=web_context, memory_context=memory_context,
                use_web=use_web, use_memory=use_memory,
                temp=temp, include_history=True, num_ctx=num_ctx,
                history=history, warning_callback=warning_callback,
            )
            yield result
        else:
            raise


# ═══════════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════════

def clean_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```python\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"```$", "", text).strip()
    return text


def safe_json_parse(text: str):
    import json
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


@lru_cache(maxsize=1)
def get_available_models() -> Dict[str, str]:
    """Получает ВСЕ модели: static + ollama. Никогда не теряет модели из конфига."""
    from .config import STATIC_MODEL_DESCRIPTIONS

    # Начинаем со статических — они ВСЕГДА в списке
    models = dict(STATIC_MODEL_DESCRIPTIONS)

    # Добавляем из ollama те, которых нет в статике
    try:
        result = ollama.list()
        raw_models = []
        if hasattr(result, "models"):
            raw_models = result.models or []
        elif isinstance(result, dict):
            raw_models = result.get("models", [])

        for m in raw_models:
            name = ""
            if hasattr(m, "model"):
                name = m.model
            elif hasattr(m, "name"):
                name = m.name
            elif isinstance(m, dict):
                name = m.get("model", "") or m.get("name", "")
            name = (name or "").strip()
            if name and name not in models:
                models[name] = f"◌ {name}"
    except Exception:
        pass

    return dict(sorted(models.items(), key=lambda x: x[0].lower()))


def split_models_by_type(models: Dict[str, str]):
    """Разделяет модели на локальные и облачные."""
    local = {}
    cloud = {}
    for name, desc in models.items():
        if "cloud" in name.lower() or desc.startswith("△"):
            cloud[name] = desc
        else:
            local[name] = desc
    return local, cloud
