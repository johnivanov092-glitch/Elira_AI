"""Browser traversal tool helpers.

Extracted from core/agents.py: goal keyword extraction, page payload
assembly, link collection/ranking, and bounded multi-page browser reads.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

from app.core.files import truncate_text
from app.domain.tools.browser_action_tool import browser_runtime_hint, sync_playwright


def goal_keywords(goal: str) -> List[str]:
    words = re.findall(r"[\wа-яА-ЯёЁ-]+", (goal or "").lower())
    stop = {
        "и", "или", "в", "на", "с", "по", "для", "о", "об", "не", "это", "как", "что",
        "the", "and", "for", "with", "from", "into", "about", "read", "page", "website",
        "про", "страницу", "сайт", "сделай", "краткий", "анализ", "прочитай", "найди", "нужно",
    }
    return [word for word in words if len(word) >= 3 and word not in stop][:12]


def extract_page_payload(page, max_chars: int = 9000) -> str:
    title = ""
    try:
        title = page.title()
    except Exception:
        pass

    headings = []
    try:
        headings = page.locator("h1, h2, h3").evaluate_all(
            "els => els.slice(0,12).map(e => (e.innerText || '').trim()).filter(Boolean)"
        )
    except Exception:
        pass

    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        body_text = ""

    lines = []
    if title:
        lines.append(f"Заголовок: {title}")
    lines.append(f"URL: {page.url}")
    if headings:
        lines.append("Подзаголовки:\n- " + "\n- ".join(headings[:10]))
    if body_text.strip():
        lines.append("Текст страницы:\n" + truncate_text(body_text, max_chars))
    return "\n\n".join(lines)


def collect_links(page, base_url: str) -> List[Dict[str, Any]]:
    try:
        links = page.locator("a").evaluate_all(
            """els => els.slice(0,150).map(a => ({
                text: (a.innerText || '').trim(),
                href: a.href || a.getAttribute('href') || '',
                title: a.getAttribute('title') || ''
            }))"""
        )
    except Exception:
        return []

    cleaned: List[Dict[str, Any]] = []
    seen = set()
    base_host = urlparse(base_url).netloc.lower()
    for item in links:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        href = urljoin(base_url, href)
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen:
            continue
        seen.add(href)
        parsed = urlparse(href)
        cleaned.append({
            "href": href,
            "text": (item.get("text") or "").strip(),
            "title": (item.get("title") or "").strip(),
            "same_domain": parsed.netloc.lower() == base_host,
        })
    return cleaned


def score_link(link: Dict[str, Any], keywords: List[str]) -> int:
    bag = f"{link.get('text', '')} {link.get('title', '')} {link.get('href', '')}".lower()
    score = 0
    if link.get("same_domain"):
        score += 3
    if any(marker in bag for marker in ["about", "pricing", "docs", "product", "contact", "blog", "features", "faq"]):
        score += 1
    for keyword in keywords:
        if keyword in bag:
            score += 4
    if any(marker in bag for marker in ["login", "signup", "register", "signin"]):
        score -= 4
    if any(marker in bag for marker in ["facebook", "twitter", "instagram", "linkedin", "youtube", "t.me"]):
        score -= 3
    return score


def rank_links(links: List[Dict[str, Any]], goal: str, limit: int) -> List[Dict[str, Any]]:
    keywords = goal_keywords(goal)
    ranked = [{**link, "score": score_link(link, keywords)} for link in links]
    ranked.sort(
        key=lambda item: (item["score"], item.get("same_domain", False), len(item.get("text", ""))),
        reverse=True,
    )
    positives = [item for item in ranked if item["score"] > 0]
    return (positives or ranked)[:limit]


def run_browser_agent(start_url: str, goal: str, max_pages: int = 3) -> Dict[str, Any]:
    trace: List[Dict[str, Any]] = []
    if sync_playwright is None:
        return {
            "ok": False,
            "text": "Playwright не установлен.\nЗапусти: pip install playwright && playwright install",
            "trace": [],
        }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(start_url, wait_until="load", timeout=30000)
            page.wait_for_timeout(1200)

            trace.append({"step": 1, "action": "open", "url": page.url, "title": page.title()})
            collected = ["=== Страница 1 ===\n" + extract_page_payload(page, max_chars=10000)]

            ranked_links = rank_links(collect_links(page, page.url), goal, limit=max(0, max_pages - 1))
            visited = {page.url}

            for idx, link in enumerate(ranked_links, start=2):
                href = link.get("href", "")
                if not href or href in visited:
                    continue
                visited.add(href)
                sub = None
                try:
                    sub = context.new_page()
                    sub.goto(href, wait_until="load", timeout=25000)
                    sub.wait_for_timeout(1000)
                    collected.append(
                        "\n\n=== Страница {idx} ===\n".format(idx=idx)
                        + extract_page_payload(sub, max_chars=8000)
                    )
                    trace.append({
                        "step": idx,
                        "action": "open_link",
                        "url": sub.url,
                        "title": sub.title(),
                        "score": link.get("score", 0),
                        "link_text": link.get("text", ""),
                    })
                except Exception as exc:
                    trace.append({
                        "step": idx,
                        "action": "error",
                        "url": href,
                        "title": str(exc),
                        "score": link.get("score", 0),
                        "link_text": link.get("text", ""),
                    })
                finally:
                    try:
                        if sub is not None:
                            sub.close()
                    except Exception:
                        pass

            browser.close()
            return {"ok": True, "text": truncate_text("\n".join(collected), 30000), "trace": trace}
    except Exception as exc:
        return {"ok": False, "text": browser_runtime_hint(exc), "trace": trace}
