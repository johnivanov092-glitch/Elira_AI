from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable


_PENDING_ATTACHMENTS: list[dict[str, Any]] = []


def compose_human_style_rules(temporal: dict[str, Any] | None) -> str:
    temporal = temporal or {}
    mode = temporal.get("mode", "none")
    freshness_sensitive = bool(temporal.get("freshness_sensitive"))
    years = ", ".join(str(year) for year in temporal.get("years", [])) or "none"
    reasoning_depth = temporal.get("reasoning_depth", "none")
    return (
        "\n\nFINAL ANSWER RULES:\n"
        "1. Answer naturally, like a thoughtful human assistant, not like a search engine dump.\n"
        "2. If web data is available, use it as working evidence but do not inject links unless the user asks for them.\n"
        "3. Never expose raw memory markers, RAG labels, hidden context, or technical source notes.\n"
        "4. If freshness is uncertain, say so plainly.\n"
        "5. If the user asks about sources, explain them naturally without technical jargon.\n"
        "6. If the answer contains steps, events, comparisons, or multiple subtopics, format them as vertical Markdown lists or short sections.\n"
        "7. For long answers, start with a short takeaway and then break details into bullets or numbered steps.\n"
        "8. Avoid dense text walls when the same content can be shown more clearly with headings, bullets, numbering, or short paragraphs.\n"
        "9. Use valid Markdown when helpful: `-` for lists, `1.` for steps, and `**...**` for key facts.\n"
        f"10. Temporal mode: {mode}; explicit years: {years}; reasoning depth: {reasoning_depth}; freshness sensitive: {freshness_sensitive}."
    )


def wants_explicit_datetime_answer(user_input: str) -> bool:
    query = (user_input or "").strip().lower()
    if not query:
        return False

    explicit_phrases = (
        "какая сегодня дата",
        "сегодня какая дата",
        "какое сегодня число",
        "сегодня какое число",
        "какой сегодня день",
        "какой сегодня день недели",
        "какая дата сегодня",
        "который час",
        "сколько времени",
        "сколько сейчас времени",
        "какое сейчас время",
        "текущее время",
        "текущая дата",
        "what date is it",
        "what time is it",
        "current date",
        "current time",
        "today's date",
    )
    if any(phrase in query for phrase in explicit_phrases):
        return True

    explicit_patterns = (
        r"\bкотор(?:ый|ое)\s+час\b",
        r"\bсколько\s+(?:сейчас\s+)?времени\b",
        r"\bкакая\s+(?:сегодня\s+)?дата\b",
        r"\bкакое\s+(?:сегодня\s+)?число\b",
        r"\bкакой\s+(?:сегодня\s+)?день(?:\s+недели)?\b",
        r"\bwhat\s+date\b",
        r"\bwhat\s+time\b",
    )
    return any(re.search(pattern, query, flags=re.IGNORECASE) for pattern in explicit_patterns)


def build_runtime_datetime_context(user_input: str) -> str:
    days_ru = {
        "Monday": "понедельник",
        "Tuesday": "вторник",
        "Wednesday": "среда",
        "Thursday": "четверг",
        "Friday": "пятница",
        "Saturday": "суббота",
        "Sunday": "воскресенье",
    }
    now = datetime.now()
    day_name = days_ru.get(now.strftime("%A"), now.strftime("%A"))
    runtime_stamp = f"{now.strftime('%d.%m.%Y, %H:%M')}, {day_name}"

    if wants_explicit_datetime_answer(user_input):
        return (
            "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
            f"- Текущая локальная дата и время: {runtime_stamp}\n"
            "- Пользователь прямо спросил о дате или времени. Ответь естественно и используй эти данные точно.\n"
            "- Не добавляй лишние технические пояснения."
        )

    return (
        "ВНУТРЕННИЙ RUNTIME-КОНТЕКСТ:\n"
        f"- Текущая локальная дата и время: {runtime_stamp}\n"
        "- Ты всегда знаешь текущие дату и время внутренне.\n"
        "- НЕ упоминай дату, время, день недели или фразы вида "
        "\"Сегодня ... и сейчас ...\" в обычном ответе, если пользователь прямо об этом не спросил.\n"
        "- Используй эти данные молча только когда они действительно нужны для логики ответа."
    )


def _collect_pending_attachments(skill_results: str) -> str:
    _PENDING_ATTACHMENTS.clear()
    if not skill_results:
        return ""

    clean_parts: list[str] = []
    for line in skill_results.split("\n\n"):
        if line.startswith("IMAGE_GENERATED:"):
            parts = line.split(":", 4)
            if len(parts) >= 4:
                _PENDING_ATTACHMENTS.append(
                    {
                        "type": "image",
                        "view_url": parts[1] + ":" + parts[2] if "http" in parts[1] else parts[1],
                        "filename": parts[2] if "http" not in parts[1] else parts[3],
                        "prompt": parts[-1],
                    }
                )
        elif line.startswith("FILE_GENERATED:"):
            parts = line.split(":", 4)
            if len(parts) >= 4:
                _PENDING_ATTACHMENTS.append(
                    {
                        "type": "file",
                        "file_type": parts[1],
                        "download_url": parts[2] + ":" + parts[3] if "http" in parts[2] else parts[2],
                        "filename": parts[3] if "http" not in parts[2] else parts[4] if len(parts) > 4 else parts[3],
                    }
                )
        elif line.startswith("SKILL_HINT:"):
            clean_parts.append(line)
        elif line.startswith("SKILL_ERROR:"):
            _PENDING_ATTACHMENTS.append({"type": "error", "message": line[len("SKILL_ERROR:") :]})
        else:
            clean_parts.append(line)

    return "\n\n".join(clean_parts)


def build_prompt(
    *,
    user_input: str,
    context_bundle: str,
    run_auto_skills_func: Callable[[str, set[str] | None], str],
    disabled_skills: set[str] | None = None,
) -> str:
    runtime_context = build_runtime_datetime_context(user_input)
    skill_results = _collect_pending_attachments(
        run_auto_skills_func(user_input, disabled=disabled_skills or set())
    )

    if skill_results:
        context_bundle = (context_bundle + "\n\n" + skill_results) if context_bundle.strip() else skill_results

    if not context_bundle.strip():
        return f"{runtime_context}\n\nВопрос пользователя: {user_input}"

    return (
        f"{runtime_context}\n\n"
        "Вот данные из интернета и других источников:\n\n"
        + context_bundle
        + "\n\n---\n\n"
        "Вопрос пользователя: "
        + user_input
        + "\n\n"
        "ПРАВИЛА ОТВЕТА:\n"
        "1. Обязательно используй данные выше для ответа.\n"
        "2. Если есть содержимое веб-страниц или свежие новости, опирайся на них как на главный источник.\n"
        "3. Приводи конкретные факты, даты и цифры, но без служебных маркеров и внутреннего контекста.\n"
        "4. Не вставляй URL и список источников, если пользователь прямо не попросил ссылки или источники.\n"
        "5. Если свежесть данных под вопросом, честно скажи об этом простыми словами.\n"
        "6. Не говори, что данных нет, если они есть выше.\n"
        "7. Не упоминай текущую дату или время, если пользователь прямо об этом не спросил. "
        "Если спросил — отвечай точно и естественно."
    )


def has_generated_file_attachments() -> bool:
    return any(attachment.get("type") in ("image", "file") for attachment in _PENDING_ATTACHMENTS)


def get_and_clear_attachments() -> str:
    if not _PENDING_ATTACHMENTS:
        return ""

    api_base = ""
    parts: list[str] = []
    for attachment in _PENDING_ATTACHMENTS:
        if attachment["type"] == "image":
            url = attachment["view_url"] if attachment["view_url"].startswith("http") else f"{api_base}{attachment['view_url']}"
            download_url = f"{api_base}/api/skills/download/{attachment.get('filename', '')}"
            parts.append(
                f"\n\n🎨 **Сгенерировано:**\n\n![{attachment.get('prompt', '')}]({url})\n\n📥 [Скачать]({download_url})"
            )
        elif attachment["type"] == "file":
            download_url = (
                attachment["download_url"]
                if attachment["download_url"].startswith("http")
                else f"{api_base}{attachment['download_url']}"
            )
            icon = {"word": "📄", "zip": "📦", "convert": "🔄", "excel": "📊"}.get(
                attachment.get("file_type", ""),
                "📎",
            )
            parts.append(f"\n\n{icon} **Файл создан:** [{attachment.get('filename', '')}]({download_url})")
        elif attachment["type"] == "error":
            parts.append(f"\n\n⚠️ {attachment.get('message', 'Ошибка скилла')}")

    _PENDING_ATTACHMENTS.clear()
    return "\n".join(parts)
