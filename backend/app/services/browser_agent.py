from __future__ import annotations

import asyncio
import time
from typing import Any

from app.services.web_service import research_web

try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None


class BrowserAgent:
    """
    Upgraded browser agent.

    Backward compatible with existing BrowserAgent.search(), but can also
    execute lightweight Playwright sessions when playwright is installed.
    """

    def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "error": "Empty query",
            }

        try:
            results = research_web(query=query, max_results=max_results)
        except Exception as exc:
            return {
                "ok": False,
                "query": query,
                "results": [],
                "count": 0,
                "error": str(exc),
            }

        return {
            "ok": True,
            "mode": "search",
            "query": query,
            "results": results if isinstance(results, list) else [],
            "count": len(results) if isinstance(results, list) else 0,
        }

    def run(self, start_url: str, steps: list[dict[str, Any]] | None = None, headless: bool = True) -> dict[str, Any]:
        if async_playwright is None:
            return {
                "ok": False,
                "mode": "browser_run",
                "error": "Playwright is not installed. Run: pip install playwright && playwright install",
                "start_url": start_url,
                "steps": steps or [],
            }

        try:
            return asyncio.run(self._run_async(start_url=start_url, steps=steps or [], headless=headless))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(self._run_async(start_url=start_url, steps=steps or [], headless=headless))
            finally:
                loop.close()
                asyncio.set_event_loop(None)
        except Exception as exc:
            return {
                "ok": False,
                "mode": "browser_run",
                "error": str(exc),
                "start_url": start_url,
                "steps": steps or [],
            }

    async def _run_async(self, start_url: str, steps: list[dict[str, Any]], headless: bool) -> dict[str, Any]:
        timeline: list[dict[str, Any]] = []
        started_at = time.time()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            if start_url:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                timeline.append({
                    "action": "goto",
                    "url": page.url,
                    "title": await page.title(),
                })

            extracted: dict[str, Any] = {}

            for index, step in enumerate(steps, start=1):
                action = str(step.get("action", "")).strip().lower()
                selector = str(step.get("selector", "")).strip()
                value = step.get("value")
                timeout = int(step.get("timeout_ms", 10000))

                if action == "goto":
                    await page.goto(str(value or selector or start_url), wait_until="domcontentloaded", timeout=30000)
                    timeline.append({"step": index, "action": action, "url": page.url})
                elif action == "click":
                    await page.click(selector, timeout=timeout)
                    timeline.append({"step": index, "action": action, "selector": selector})
                elif action == "fill":
                    await page.fill(selector, str(value or ""), timeout=timeout)
                    timeline.append({"step": index, "action": action, "selector": selector})
                elif action == "press":
                    await page.press(selector, str(value or "Enter"), timeout=timeout)
                    timeline.append({"step": index, "action": action, "selector": selector, "value": value})
                elif action == "wait_for":
                    await page.wait_for_selector(selector, timeout=timeout)
                    timeline.append({"step": index, "action": action, "selector": selector})
                elif action == "extract_text":
                    text = await page.text_content(selector, timeout=timeout)
                    extracted[step.get("name") or f"text_{index}"] = text or ""
                    timeline.append({"step": index, "action": action, "selector": selector, "chars": len(text or "")})
                elif action == "extract_html":
                    html = await page.locator(selector).inner_html(timeout=timeout)
                    extracted[step.get("name") or f"html_{index}"] = html or ""
                    timeline.append({"step": index, "action": action, "selector": selector, "chars": len(html or "")})
                elif action == "extract_links":
                    locator = page.locator(selector or "a")
                    count = await locator.count()
                    links: list[dict[str, Any]] = []
                    for i in range(min(count, int(step.get("limit", 20)))):
                        item = locator.nth(i)
                        href = await item.get_attribute("href")
                        text = await item.text_content()
                        links.append({"href": href or "", "text": (text or "").strip()})
                    extracted[step.get("name") or f"links_{index}"] = links
                    timeline.append({"step": index, "action": action, "count": len(links)})
                elif action == "screenshot":
                    path = str(step.get("path") or f"browser_step_{index}.png")
                    await page.screenshot(path=path, full_page=bool(step.get("full_page", True)))
                    extracted[step.get("name") or f"screenshot_{index}"] = path
                    timeline.append({"step": index, "action": action, "path": path})
                else:
                    timeline.append({"step": index, "action": action, "error": "Unsupported action"})

            title = await page.title()
            final_url = page.url
            await context.close()
            await browser.close()

        return {
            "ok": True,
            "mode": "browser_run",
            "start_url": start_url,
            "final_url": final_url,
            "title": title,
            "timeline": timeline,
            "extracted": extracted,
            "duration_ms": int((time.time() - started_at) * 1000),
            "headless": headless,
            "playwright_available": True,
        }
