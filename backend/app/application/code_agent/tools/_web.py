"""Thin web entry points into the mutable web-research skill CLI."""
from __future__ import annotations

import hashlib
import json
import math
import queue
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
    scope = hashlib.sha256((run_id or str(project_root.resolve())).encode()).hexdigest()[:32]
    command = [_skill_python(root), str(script), "--state-dir",
               str(project_root / ".web-research" / scope), "--research", "--native",
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


def tool_web_search(project_root: Path, *, queries: list[str], audience: str = "global",
                    time_range: str = "", categories: str = "", mode: str = "quick") -> dict[str, Any]:
    return _call(project_root, "search", ["--mode", mode, "--audience", audience,
        "--time-range", time_range, "--categories", categories,
        *[part for query in queries for part in ("--query", query)]])


def _page_key(url: str) -> str:
    """Same page despite http/https, www, case of host or a trailing slash."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return str(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"


def _redirected_requests(urls: list[str], result: dict[str, Any]) -> list[str]:
    """Requested addresses whose read page is a different one (a redirect)."""
    sources = [source for source in result.get("sources") or []
               if isinstance(source, dict) and isinstance(source.get("url"), str)]
    if not sources:
        return []
    opened = {_page_key(source["url"]) for source in sources}
    return [url for url in urls if isinstance(url, str) and _page_key(url) not in opened]


def tool_web_fetch(project_root: Path, *, urls: list[str], find: str = "",
                   mode: str | None = None) -> dict[str, Any]:
    result = _call(project_root, "fetch", ["--find", find,
        *(["--mode", mode] if mode is not None else []),
        *[part for url in urls for part in ("--url", url)]])
    redirected = _redirected_requests(urls, result)
    if redirected:
        # The reader returns the page the server actually served; say so plainly,
        # or the model cites the requested (possibly invented) address as read.
        notes = "\n".join(
            f"Адрес {url} не открылся как запрошено: сервер вернул другую страницу (её адрес и заголовок "
            "указаны в «fetched» выше). Это не та страница — не ссылайся на этот адрес и не считай его прочитанным."
            for url in redirected)
        result = {**result, "redirected": redirected,
                  "text": (str(result.get("text") or "").rstrip() + "\n\n" + notes).lstrip()}
    return result
