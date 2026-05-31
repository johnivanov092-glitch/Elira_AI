"""Browser action tool helpers.

Extracted from core/agents.py: action sanitization, plan generation,
Playwright availability checks, and bounded browser action execution.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urljoin

from app.core.files import truncate_text
from app.core.llm import ask_model, clean_code_fence, safe_json_parse

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


def browser_runtime_hint(exc: Exception | str) -> str:
    text = str(exc or "")
    low = text.lower()
    if isinstance(exc, NotImplementedError) or "_make_subprocess_transport" in low or "notimplementederror" in low:
        return (
            "Playwright не смог запустить subprocess браузера. "
            "На Windows для Streamlit нужно включить WindowsProactorEventLoopPolicy "
            "до импорта streamlit и затем полностью перезапустить приложение."
        )
    if "executable doesn't exist" in low or "browsertype.launch" in low:
        return (
            "Браузер Chromium для Playwright не установлен. "
            "Запусти: playwright install chromium"
        )
    return text


def sync_playwright_available() -> bool:
    return sync_playwright is not None


def sanitize_browser_actions(actions: List[dict]) -> List[dict]:
    allowed = {"open", "click", "fill", "extract", "wait"}
    cleaned: List[dict] = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        if action not in allowed:
            continue
        try:
            ms = int(item.get("ms", 1000) or 1000)
        except Exception:
            ms = 1000
        cleaned.append({
            "action": action,
            "url": str(item.get("url", "")).strip(),
            "selector": str(item.get("selector", "")).strip(),
            "value": str(item.get("value", "")),
            "ms": ms,
        })
    return cleaned[:12]


def browser_actions_from_goal(goal: str, model_name: str) -> List[dict]:
    prompt = (
        "Составь план действий браузера в JSON-массиве.\n"
        "Разрешенные действия: open, click, fill, extract, wait.\n"
        "Каждый шаг должен быть минимальным и безопасным.\n"
        "Для чтения контента чаще используй extract с селекторами: main, article, body, h1.\n"
        "Не используй неизвестные действия. Верни только JSON без пояснений.\n\n"
        "Пример:\n"
        '[{"action":"extract","selector":"main"},\n'
        ' {"action":"extract","selector":"body"}]\n\n'
        f"Цель:\n{goal}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="Оркестратор",
        user_input=prompt,
        temp=0.1,
        include_history=False,
    )
    raw = clean_code_fence(re.sub(r"^```json\s*", "", raw.strip()))
    data = safe_json_parse(raw)
    return sanitize_browser_actions(data if isinstance(data, list) else [])


def run_browser_actions(start_url: str, actions: List[dict]) -> Dict[str, Any]:
    trace: List[dict[str, Any]] = []
    extracted: List[str] = []
    if sync_playwright is None:
        return {"ok": False, "text": "Playwright не установлен.", "trace": []}

    safe_actions = sanitize_browser_actions(actions)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(start_url, wait_until="load", timeout=30000)
            page.wait_for_timeout(1000)
            trace.append({"step": 1, "action": "open", "detail": page.url})

            for idx, item in enumerate(safe_actions, start=2):
                action = item.get("action", "")
                try:
                    if action == "open" and item.get("url"):
                        target = urljoin(page.url, item["url"])
                        page.goto(target, wait_until="load", timeout=30000)
                        page.wait_for_timeout(800)
                        trace.append({"step": idx, "action": "open", "detail": page.url})
                    elif action == "click" and item.get("selector"):
                        page.locator(item["selector"]).first.click(timeout=10000)
                        page.wait_for_timeout(800)
                        trace.append({"step": idx, "action": "click", "detail": item["selector"]})
                    elif action == "fill" and item.get("selector"):
                        page.locator(item["selector"]).first.fill(str(item.get("value", "")), timeout=10000)
                        trace.append({"step": idx, "action": "fill", "detail": item["selector"]})
                    elif action == "wait":
                        page.wait_for_timeout(max(100, min(int(item.get("ms", 1000)), 10000)))
                        trace.append({"step": idx, "action": "wait", "detail": item.get("ms")})
                    elif action == "extract":
                        selector = item.get("selector", "body") or "body"
                        text = page.locator(selector).first.inner_text(timeout=10000)
                        extracted.append(f"EXTRACT {selector}:\nURL: {page.url}\n" + truncate_text(text, 7000))
                        trace.append({"step": idx, "action": "extract", "detail": selector})
                except Exception as exc:
                    trace.append({"step": idx, "action": f"{action}_error", "detail": str(exc)})

            browser.close()
            return {
                "ok": True,
                "text": "\n\n".join(extracted) if extracted else "Действия выполнены, но текста для извлечения не было.",
                "trace": trace,
            }
    except Exception as exc:
        return {"ok": False, "text": browser_runtime_hint(exc), "trace": trace}
