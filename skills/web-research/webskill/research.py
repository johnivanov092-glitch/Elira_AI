"""Run-local research budgets and page reuse, shared by CLI and native entries."""
from __future__ import annotations

import hashlib
import json
import time
import threading
import uuid
import contextvars
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from webskill import context
from webskill.application.code_agent.answer_contracts import _web_source_url_key
from webskill.infrastructure.search.web_runtime import PageFetchResult

_DEADLINE: float | None = None
_STEP = 0
_CACHE_HITS: dict[str, int] = {}
_READ_PAGES: set[str] = set()
_PAGE_LOCKS: dict[str, threading.Lock] = {}
_PAGE_DEADLINE = contextvars.ContextVar('web_page_deadline', default=None)
_STOP = "Лимит времени исследования 60 с исчерпан — отвечай по прочитанному и отметь непроверенное."
_WARN = "Прошло 30 с: заверши по прочитанному, перечисли, что не проверено."


def remaining_timeout(maximum: float = 20) -> float:
    remaining = maximum if _DEADLINE is None else min(maximum, _DEADLINE - time.time())
    page_deadline = _PAGE_DEADLINE.get()
    if page_deadline is not None:
        remaining = min(remaining, page_deadline - time.time())
    if remaining <= 0:
        raise TimeoutError(_STOP)
    return max(0.01, remaining)


def reset() -> None:
    global _DEADLINE, _STEP
    _DEADLINE, _STEP = None, 0
    _CACHE_HITS.clear()
    _READ_PAGES.clear()
    _PAGE_LOCKS.clear()


@contextmanager
def _page_deadline():
    token = _PAGE_DEADLINE.set(time.time() + 20)
    try:
        yield
    finally:
        _PAGE_DEADLINE.reset(token)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cached_page(url: str, limit: int, fetch: Callable[[str, int], PageFetchResult],
                *, force_refresh: bool = False) -> PageFetchResult:
    if _DEADLINE is None:
        with _page_deadline():
            return fetch(url, limit)
    # Anchors select different sections, so their extracted views have separate
    # cache keys. Research quotas still count the fragment-free network page.
    with _PAGE_LOCKS.setdefault(url, threading.Lock()):
        cache_allowed = context.web_cache_write_allowed(context.run_persistence_policy(context.run_id()))
        path = context.data_file("page-cache") / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if cache_allowed and path.is_file() and not force_refresh:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("url") == url and isinstance(row.get("page"), dict):
                page = PageFetchResult(**row["page"])
                if page.ok:
                    _READ_PAGES.add(_web_source_url_key(url))
                _CACHE_HITS[url] = int(row.get("step") or 0)
                return replace(page, text=page.text[:limit], truncated=page.truncated or len(page.text) > limit)
        # Retain extraction success before find selects or rejects a phrase.
        with _page_deadline():
            page = fetch(url, max(limit, 200000) if cache_allowed else limit)
        if page.ok:
            _READ_PAGES.add(_web_source_url_key(url))
        if cache_allowed:
            _write(path, {"url": url, "step": _STEP, "page": asdict(page)})
        return replace(page, text=page.text[:limit], truncated=page.truncated or len(page.text) > limit)


def execute(action: str, args, operation: Callable[[], dict]) -> dict:
    global _DEADLINE, _STEP
    targets = []
    if action == "fetch":
        from webskill.application.code_agent.tools._web import WebArgumentFormatError, normalize_web_tool_arguments
        try:
            urls = normalize_web_tool_arguments("web_fetch", {"urls": getattr(args, "url", None)})["urls"]
        except WebArgumentFormatError as exc:
            return {"ok": False, "error": "argument_format", "text": f"ERROR: {exc}"}
        find = getattr(args, "find", "")
        if not isinstance(find, str) or len(find) > 200:
            return {"ok": False, "error": "argument_format", "text": "find: строка до 200 символов."}
        if find.strip() and getattr(args, "store", False):
            return {"ok": False, "error": "argument_format", "text": "find допустим только при store=false."}
        if getattr(args, "extraction_path", "") and len(urls) != 1:
            return {"ok": False, "error": "argument_format", "text": "extraction_path допустим только при одном URL."}
        targets = list(dict.fromkeys(_web_source_url_key(url) for url in urls))
    requested_mode = getattr(args, "mode", None)
    if requested_mode is not None and requested_mode not in {"quick", "deep"}:
        return {"ok": False, "error": "argument_format", "text": "mode: quick или deep"}
    path = context.data_file("research.json")
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "started_at": time.time(), "mode": requested_mode or "quick", "searches": 0, "fetches": 0, "urls": [],
        "read_urls": [], "failed_urls": [],
    }
    # Resume old runs without reopening search or changing their existing quota.
    state.pop("language_correction", None)
    # The first web call fixes this run's budget, including fetch-only research.
    # Later declarations cannot expand quick or shrink deep.
    mode = state["mode"]
    for field in ("urls", "read_urls", "failed_urls"):
        state[field] = list(dict.fromkeys(_web_source_url_key(url) for url in state.get(field, [])))
    _DEADLINE = float(state["started_at"]) + 60
    _STEP = int(getattr(args, "step", 0) or 0)
    _CACHE_HITS.clear()
    _READ_PAGES.clear()
    _PAGE_LOCKS.clear()
    elapsed = max(0, time.time() - float(state["started_at"]))
    error, hint = "", ""
    if elapsed >= 60:
        error, hint = "web_time_budget", _STOP
    elif action == "search":
        if state["searches"] >= (1 if mode == "quick" else 3):
            error, hint = "web_round_budget", "Раунды поиска исчерпаны — прочитай найденное и отвечай по собранному."
        else:
            state["searches"] += 1
    elif action == "fetch":
        maximum = 3 if mode == "quick" else 8
        if len(targets) > maximum:
            error, hint = "web_batch_budget", f"Режим {mode}: передай до {maximum} URL одним вызовом."
        else:
            new = [url for url in targets if url not in state["urls"]]
            quick_replacement = (mode == "quick" and state["fetches"] == 1
                                 and bool(state.get("failed_urls")))
            allowed_rounds = 2 if quick_replacement else 1 if mode == "quick" else 3
            # Only quick replacement excludes unavailable pages from its site
            # count. Deep research keeps the approved cap of 20 attempted URLs.
            used_sites = len(state.get("read_urls", [])) if mode == "quick" else len(state["urls"])
            if new and (state["fetches"] >= allowed_rounds
                        or used_sites + len(new) > (3 if mode == "quick" else 20)):
                error, hint = "web_site_budget", "Бюджет чтения исчерпан — отвечай по прочитанному и отметь пробелы."
            elif new:
                state["fetches"] += 1
                state["urls"].extend(new)
    _write(path, state)
    if getattr(args, 'native', False):
        print(json.dumps({'web_deadline_epoch': _DEADLINE}), flush=True)
    if error:
        result = {"ok": False, "error": error, "text": hint}
    else:
        result = operation()
    if action == "fetch" and not error:
        for page in result.get("pages") or []:
            if isinstance(page, dict) and page.get("url"):
                url = _web_source_url_key(page["url"])
                read = page.get("ok") or url in _READ_PAGES
                ledger = state.setdefault("read_urls" if read else "failed_urls", [])
                if url not in ledger:
                    ledger.append(url)
        _write(path, state)
    elapsed = max(0, time.time() - float(state["started_at"]))
    warning = _STOP if elapsed >= 60 else _WARN if elapsed >= 30 else ""
    cached = [{"url": url, "step": step} for url, step in _CACHE_HITS.items()]
    notes = [f"{url}: уже прочитано на шаге {step} (кэш прогона)." for url, step in _CACHE_HITS.items()]
    text = str(result.get("text") or "")
    if warning and not text.startswith(warning):
        text = warning + "\n\n" + text
    if not error and result.get("ok") is True and action in {"search", "fetch"}:
        # The model sees what is left, instead of spending a call on a refusal
        # (SWE run c6166f63: a second quick search cost 2.5 s past the 20 s norm).
        searches_left = max(0, (1 if mode == "quick" else 3) - state["searches"])
        rounds = (2 if state["fetches"] == 1 and state.get("failed_urls") else 1) if mode == "quick" else 3
        reads_left = max(0, rounds - state["fetches"])
        budget = f"Бюджет ({mode}): поисков осталось {searches_left}, чтений {reads_left}."
        if not searches_left and not reads_left:
            budget += " Отвечай по прочитанному."
        elif not searches_left:
            budget += " Новый поиск не пройдёт: читай найденное."
        text = text.rstrip() + "\n\n" + budget
    from webskill.context import WEB_TOOL_RESULT_LLM_LIMIT
    cache_notice = "\n".join(notes)
    notice_room = max(0, WEB_TOOL_RESULT_LLM_LIMIT - len(text) - 2)
    if len(cache_notice) > notice_room:
        cache_notice = f"Из кэша прогона прочитано страниц: {len(cached)}."
    if len(cache_notice) > notice_room:
        cache_notice = ""
    return {**result, "text": text + ("\n\n" + cache_notice if cache_notice else ""),
            "web_research": {"mode": mode, "elapsed_seconds": round(elapsed, 3), "warning": warning,
                "stopped": elapsed >= 60, "search_rounds": state["searches"],
                "read_rounds": state["fetches"], "sites": len(state.get("read_urls", [])), "cached": cached}}
