"""Thin web entry points into the mutable web-research skill CLI."""
from __future__ import annotations

import hashlib
import json
import math
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.application.agent_kernel.runtime_context import current_runtime_context
from app.application.code_agent.tools._math import _skill_python
from app.application.code_agent.tools._run import _agent_child_env
from app.application.code_agent.tools._shell import (
    _kill_proc_tree, _new_process_group_kwargs, get_current_run_id,
    register_run_process, run_was_stopped, unregister_run_process,
)
from app.core.skill_modules import skill_root


def _call(project_root: Path, action: str, options: list[str]) -> dict[str, Any]:
    root = skill_root("web-research")
    script = root / "web.py"
    if not script.is_file():
        return {"ok": False, "error": "skill_missing", "text": f"ERROR: web-research unavailable: {script}"}
    run_id = get_current_run_id()
    command = [_skill_python(root), str(script), "--state-dir",
               str(_state_dir(project_root)), "--research", "--native",
               "--step", str(current_runtime_context().get("step") or "0"), action, *options]
    process = None
    try:
        process = subprocess.Popen(command, cwd=str(project_root), env=_agent_child_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **_new_process_group_kwargs())
        registered = register_run_process(process)
        try:
            transport_deadline = time.monotonic() + 61
            lines: queue.Queue[bytes | OSError] = queue.Queue(maxsize=1)

            def read_first_line() -> None:
                try:
                    lines.put(process.stdout.readline())
                except OSError as exc:
                    lines.put(exc)

            reader = threading.Thread(target=read_first_line, daemon=True)
            reader.start()
            first_line = None
            while first_line is None:
                remaining = transport_deadline - time.monotonic()
                if remaining <= 0 or run_was_stopped(run_id):
                    _kill_proc_tree(process)
                    reader.join()
                    process.communicate()
                    if run_was_stopped(run_id):
                        return {"ok": False, "error": "cancelled", "text": "Прервано пользователем."}
                    return {"ok": False, "error": "web_time_budget", "text":
                        "Лимит времени исследования исчерпан — отвечай по прочитанному и отметь непроверенное.",
                        "web_research": {"stopped": True}}
                try:
                    first_line = lines.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    continue
            reader.join()
            if isinstance(first_line, OSError):
                _kill_proc_tree(process)
                process.communicate()
                raise first_line
            try:
                frame = json.loads(first_line)
            except (ValueError, UnicodeError):
                frame = None
            deadline = frame.get("web_deadline_epoch") if isinstance(frame, dict) else None
            framed = type(deadline) in (int, float) and math.isfinite(deadline)
            while True:
                remaining = float(deadline) - time.time() if framed else transport_deadline - time.monotonic()
                if remaining <= 0:
                    _kill_proc_tree(process)
                    process.communicate()
                    return {"ok": False, "error": "web_time_budget", "text":
                        "Лимит времени исследования исчерпан — отвечай по прочитанному и отметь непроверенное.",
                        "web_research": {"stopped": True}}
                try:
                    stdout, stderr = process.communicate(timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    if run_was_stopped(run_id):
                        _kill_proc_tree(process)
                        process.communicate()
                        return {"ok": False, "error": "cancelled", "text": "Прервано пользователем."}
        finally:
            unregister_run_process(registered, process)
        if run_was_stopped(run_id):
            return {"ok": False, "error": "cancelled", "text": "Прервано пользователем."}
        payload = stdout if framed else first_line + stdout
        result = json.loads(payload.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("skill returned non-object JSON")
        return {**result, "ok": result.get("ok") is True and process.returncode == 0}
    except (OSError, UnicodeError, ValueError) as exc:
        from app.core.redaction import redact_text
        return {"ok": False, "error": "web_skill_failed", "text": "ERROR: " + redact_text(str(exc))[:1000]}


def _state_dir(project_root: Path) -> Path:
    run_id = get_current_run_id()
    scope = hashlib.sha256((run_id or str(project_root.resolve())).encode()).hexdigest()[:32]
    return project_root / ".web-research" / scope


def _page_key(url: str) -> str:
    """Same page despite http/https, www, case of host or a trailing slash."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return str(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"


# Run ledger of real addresses (search results, links on read pages, the user's
# messages) and of read pages with their citation numbers. John 2026-10-11:
# the web contour must make a wrong action impossible, not merely discouraged.
_LEDGER_FILE = "elira_pages.json"


def _load_ledger(project_root: Path) -> dict[str, Any]:
    try:
        value = json.loads((_state_dir(project_root) / _LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    allowed = value.get("allowed")
    pages = value.get("pages")
    refs = value.get("refs")
    return {"allowed": dict(allowed) if isinstance(allowed, dict) else {},
            "pages": [page for page in pages if isinstance(page, dict) and isinstance(page.get("url"), str)]
            if isinstance(pages, list) else [],
            # S<n> = search result, L<n> = article link on a read page: the model
            # picks a page by its label and never has to copy a long address.
            "refs": {key: url for key, url in refs.items() if isinstance(url, str)}
            if isinstance(refs, dict) else {}}


def _ref(ledger: dict[str, Any], prefix: str, url: str) -> str:
    """Stable label for an address within this run (S3, L12)."""
    key = _page_key(url)
    for label, known in ledger["refs"].items():
        if label.startswith(prefix) and _page_key(known) == key:
            return label
    label = f"{prefix}{1 + sum(1 for name in ledger['refs'] if name.startswith(prefix))}"
    ledger["refs"][label] = url
    return label


_REF_ARGUMENT = re.compile(r"^\[?([SL]\d{1,4})\]?$", re.IGNORECASE)
_SEARCH_ITEM = re.compile(r"^\[(\d+)\] (.*)\n(\s+)(https?://\S+)", re.MULTILINE)


def _save_ledger(project_root: Path, ledger: dict[str, Any]) -> None:
    path = _state_dir(project_root) / _LEDGER_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _allow(ledger: dict[str, Any], url: Any) -> None:
    if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
        ledger["allowed"].setdefault(_page_key(url), url)


_USER_URL = re.compile(r"https?://[^\s<>\"'«»()\[\]]+")


def _user_urls() -> list[str]:
    try:
        urls = json.loads(current_runtime_context().get("user_urls") or "[]")
    except ValueError:
        return []
    return [url for url in urls if isinstance(url, str)] if isinstance(urls, list) else []


def user_message_urls(texts: list[str]) -> list[str]:
    """Addresses the user wrote; trusted input from the runtime, never from the model."""
    found: dict[str, None] = {}
    for text in texts:
        for match in _USER_URL.finditer(str(text or "")):
            found.setdefault(match.group(0).rstrip(".,;:!?"), None)
    return list(found)[:50]


def _is_index_page(url: str, links: list[dict[str, Any]]) -> bool:
    """A home or section page: a list of headlines, not the articles themselves."""
    segments = [part for part in urlsplit(url).path.split("/") if part]
    shallow = not segments or (len(segments) == 1 and not re.search(r"\d|\.s?html?$", segments[0]))
    return shallow and len(links) >= 3


def _article_links(url: str, links: list[dict[str, Any]], limit: int = 12) -> list[tuple[str, str]]:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    articles: list[tuple[str, str]] = []
    for link in links:
        target = str(link.get("url") or "")
        label = " ".join(str(link.get("label") or "").split())
        parts = urlsplit(target)
        if (parts.scheme not in {"http", "https"} or not label
                or (parts.hostname or "").lower().removeprefix("www.") != host):
            continue
        segments = [part for part in parts.path.split("/") if part]
        if len(segments) >= 2 or re.search(r"\d", parts.path):
            articles.append((label[:120], target))
        if len(articles) >= limit:
            break
    return articles


def tool_web_search(project_root: Path, *, queries: list[str], audience: str = "global",
                    time_range: str = "", categories: str = "", mode: str = "quick") -> dict[str, Any]:
    result = _call(project_root, "search", ["--mode", mode, "--audience", audience,
        "--time-range", time_range, "--categories", categories,
        *[part for query in queries for part in ("--query", query)]])
    ledger = _load_ledger(project_root)
    for source in result.get("sources") or []:
        if isinstance(source, dict):
            _allow(ledger, source.get("url"))

    def label(match: re.Match[str]) -> str:
        _allow(ledger, match.group(4))
        return f"[{_ref(ledger, 'S', match.group(4))}] {match.group(2)}\n{match.group(3)}{match.group(4)}"
    text = _SEARCH_ITEM.sub(label, str(result.get("text") or ""))
    _save_ledger(project_root, ledger)
    if text and "[S" in text:
        text += "\n\nОткрывай найденное по метке: web_fetch(urls=[\"S1\", \"S3\"]) — адрес подставит Elira."
    return {**result, "text": text}


def tool_web_fetch(project_root: Path, *, urls: list[str], find: str = "",
                   mode: str | None = None) -> dict[str, Any]:
    ledger = _load_ledger(project_root)
    user_keys = {_page_key(url) for url in _user_urls()}
    for url in _user_urls():
        _allow(ledger, url)
    for page in ledger["pages"]:
        _allow(ledger, page["url"])
    requested: list[str] = []
    unknown_refs: list[str] = []
    for item in urls:
        if not isinstance(item, str):
            continue
        label = _REF_ARGUMENT.match(item.strip())
        if label:
            target = ledger["refs"].get(label.group(1).upper())
            (requested if target else unknown_refs).append(target or item.strip())
        else:
            requested.append(item.strip())
    refused = [url for url in requested if _page_key(url) not in ledger["allowed"]]
    permitted = list(dict.fromkeys(url for url in requested if url not in refused))
    refusal = ""
    if refused or unknown_refs:
        hosts = {(urlsplit(url).hostname or "").lower().removeprefix("www.") for url in refused}
        nearby = [f"{name} {url}" for name, url in ledger["refs"].items()
                  if (urlsplit(url).hostname or "").lower().removeprefix("www.") in hosts][:8]
        refusal = (
            "Не открыто: " + ", ".join([*refused, *unknown_refs]) + " — такого адреса или метки нет в выдаче поиска, "
            "на прочитанных страницах и в сообщениях пользователя. Не составляй адреса сам: открывай по метке "
            "из результатов (S… — выдача поиска, L… — статья с прочитанной страницы); нужной нет — сделай web_search."
            + (" Метки этого сайта: " + "; ".join(nearby) + "." if nearby else ""))
    if not permitted:
        _save_ledger(project_root, ledger)
        return {"ok": False, "error": "url_not_from_sources", "text": refusal,
                "refused_urls": [*refused, *unknown_refs]}
    result = _call(project_root, "fetch", ["--find", find,
        *(["--mode", mode] if mode is not None else []),
        *[part for url in permitted for part in ("--url", url)]])
    numbers = {_page_key(page["url"]): index for index, page in enumerate(ledger["pages"], 1)}
    page_numbers: dict[str, int] = {}
    index_pages: list[str] = []
    index_blocks: dict[str, str] = {}
    redirected: list[str] = []
    notes: list[str] = []
    for page in result.get("pages") or []:
        if not isinstance(page, dict):
            continue
        asked = str(page.get("url") or "")
        final = str(page.get("final_url") or asked)
        links = [link for link in page.get("links") or [] if isinstance(link, dict)]
        _allow(ledger, asked)
        _allow(ledger, final)
        if page.get("ok") is not True:
            continue
        for link in links:
            _allow(ledger, link.get("url"))
        key = _page_key(final)
        title = str(page.get("title") or final)
        # A home page the user asked for by address is the page they want to read.
        if _is_index_page(final, links) and not {key, _page_key(asked)} & user_keys:
            # Headlines are not articles: the model sees only the labelled list,
            # so it has nothing to build facts from but the opened articles.
            index_pages.append(final)
            articles = [(_ref(ledger, "L", target), label) for label, target in _article_links(final, links)]
            index_blocks[key] = (
                f"[fetched: {final}]\n«{title}» — главная или рубрика: заголовки скрыты, это не содержание статей "
                "и не источник для ответа. Открой нужные статьи одним вызовом по меткам, например "
                "web_fetch(urls=[\"L1\", \"L2\"])" + (":\n" + "\n".join(f"{name} {label}" for name, label in articles)
                                                      if articles else "; ссылок на статьи нет — сделай web_search."))
            continue
        if key not in numbers:
            ledger["pages"].append({"url": final, "title": str(page.get("title") or "")[:200]})
            numbers[key] = len(ledger["pages"])
        number = numbers[key]
        page_numbers[final] = number
        if _page_key(asked) != key:
            # The server answered with another page; name it, or the model cites
            # the requested (possibly invented) address as if it had been read.
            redirected.append(asked)
            notes.append(f"Запрошен {asked}, но сервер отдал другую страницу [{number}] «{title}» ({final}). "
                         "Это не запрошенный материал — не ссылайся на запрошенный адрес и не считай его прочитанным.")
    _save_ledger(project_root, ledger)
    text = str(result.get("text") or "")
    if index_blocks:
        separator = "\n\n———\n\n"
        head, body = (text.split("\n\n", 1) if text.startswith("Fetched ") and "\n\n" in text else ("", text))
        blocks = []
        for block in body.split(separator):
            fetched = re.search(r"\[fetched: (\S+?)\]", block)
            blocks.append(index_blocks.get(_page_key(fetched.group(1)), block) if fetched else block)
        text = (head + "\n\n" if head else "") + separator.join(blocks)
    if page_numbers:
        by_key = {_page_key(url): number for url, number in page_numbers.items()}
        text = re.sub(r"\[fetched: (\S+?)\]",
                      lambda match: (f"[{by_key[_page_key(match.group(1))]}] " if _page_key(match.group(1)) in by_key
                                     else "") + match.group(0), text)
        notes.insert(0, "Ссылайся на прочитанные страницы номером [n] из этого результата — адрес подставит Elira.")
    if refusal:
        notes.append(refusal)
    if notes:
        text = (text.rstrip() + "\n\n" + "\n".join(notes)).lstrip()
    return {**result, "text": text, "page_numbers": page_numbers, "index_pages": index_pages,
            **({"redirected": redirected} if redirected else {}),
            **({"refused_urls": [*refused, *unknown_refs]} if refused or unknown_refs else {})}
