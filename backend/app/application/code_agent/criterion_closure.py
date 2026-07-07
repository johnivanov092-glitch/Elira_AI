"""Deterministic Criterion Closure + Cleanup Barrier + Final Report (Ph7.12).

A TaskSpec run is a small state machine: work → CLOSE the open criteria with the
EXACT verifier calls → cleanup → final. This module reads the CriteriaTracker and
computes, deterministically, which verifier calls are still MISSING for criteria
whose verifier is unambiguous — so the loop can spend ONE bounded closure turn asking
for those exact calls instead of letting the model finalize a partial run while
claiming success. It also owns the FINAL status block (runtime-generated, never the
model's word) and the CLEANUP barrier (don't delete a path while criteria that live
under it are still open). Pure: it never executes tools; it returns text/data.
"""
from __future__ import annotations

import re

from app.application.code_agent.taskspec import CriteriaTracker, _QUOTED_RE, _file_tokens

_HOST_PLACEHOLDER = "<host>"
_URL_PLACEHOLDER = "<actual_url от run_server>"

_COMMAND_FOR_KIND = {
    "typecheck": "npm run typecheck",
    "build": "npm run build",
    "test": "pytest / npm test",
    "import": "python -c 'import <модуль>'",
    "smoke": "smoke-проверку",
    "lint": "npm run lint",
}


def _criterion_path(item: dict) -> str:
    """The file/dir path a criterion is about — a quoted path token, else the longest
    path in item['files']."""
    quoted_paths = [q.strip() for q in _QUOTED_RE.findall(item.get("text") or "") if _file_tokens(q)]
    if quoted_paths:
        return quoted_paths[0]
    paths = [f for f in (item.get("files") or ()) if "/" in f or "\\" in f]
    pool = paths or list(item.get("files") or ())
    return max(pool, key=len) if pool else ""


def _content_pattern(item: dict) -> str:
    """The required string of a content criterion — a quoted token that is NOT a path."""
    for q in _QUOTED_RE.findall(item.get("text") or ""):
        q = q.strip()
        if q and not _file_tokens(q):
            return q
    return ""


def _action_for(item: dict, *, host: str, url: str) -> dict | None:
    """The one concrete verifier call that would close this criterion, or None when the
    verifier is not unambiguous (generic / viewport w/o evidence) — those stay honestly
    unverified rather than being demanded of the model."""
    intent, text = item["intent"], item["text"]
    if intent == "content_contains":
        path, pat = _criterion_path(item), _content_pattern(item)
        if path and pat:
            return {"tool": "ssh_assert_contains",
                    "call": f"ssh_assert_contains(host={host}, path=`{path}`, pattern=`{pat}`)", "why": text}
    elif intent == "content_not_contains":
        path, pat = _criterion_path(item), _content_pattern(item)
        if path and pat:
            return {"tool": "ssh_assert_not_contains",
                    "call": f"ssh_assert_not_contains(host={host}, path=`{path}`, pattern=`{pat}`)", "why": text}
    elif intent == "file_exists":
        path = _criterion_path(item)
        if path:
            return {"tool": "ssh_exists", "call": f"ssh_exists(host={host}, path=`{path}`)", "why": text}
    elif intent == "file_not_exists":
        path = _criterion_path(item)
        if path:
            return {"tool": "ssh_not_exists", "call": f"ssh_not_exists(host={host}, path=`{path}`)", "why": text}
    elif intent == "dom_contains":
        toks = item.get("targets") or set()
        show = ", ".join(f"`{t}`" for t in sorted(toks)) if toks else "нужный текст"
        return {"tool": "browser",
                "call": f"browser(url={url}) — в отрисованном DOM должно быть {show} (НЕ grep по бандлу)", "why": text}
    elif intent == "page_open":
        return {"tool": "http_api",
                "call": f"http_api(url={url}) или browser(url={url}) — страница должна открыться (2xx)", "why": text}
    elif intent == "server_started":
        return {"tool": "run_server",
                "call": "run_server(action=start, …) или ssh_port_check(host, port) — сервер должен подняться", "why": text}
    elif intent == "command_check":
        cmd = _COMMAND_FOR_KIND.get(item.get("command_kind") or "any", "нужную проверку")
        return {"tool": "run_bash", "call": f"run_bash(`{cmd}`) — должно пройти (exit 0)", "why": text}
    return None  # generic / viewport_layout → no deterministic verifier


def missing_verifier_actions(tracker: CriteriaTracker, *, host: str = _HOST_PLACEHOLDER,
                             url: str = _URL_PLACEHOLDER) -> list[dict]:
    """Concrete verifier calls still missing for each unconfirmed/failed criterion whose
    verifier is unambiguous. Criteria with no deterministic verifier are omitted."""
    out = []
    for it in tracker.items:
        if it["status"] == "confirmed":
            continue
        act = _action_for(it, host=host, url=url)
        if act is not None:
            out.append(act)
    return out


def missing_set_key(actions: list[dict]) -> str:
    """A stable key for a missing-set, so the closure gate fires at most once per set."""
    return "|".join(sorted(a["why"] for a in actions))


def closure_nudge_text(actions: list[dict]) -> str:
    lines = "\n".join(f"- {a['call']}  ← {a['why']}" for a in actions)
    return (
        "Перед завершением: часть критериев ещё НЕ подтверждена verifier'ом. Закрой их "
        "ТОЧНО этими вызовами (не словами; и НЕ ssh_read — он подтверждает только "
        "существование файла, а не его содержимое):\n"
        f"{lines}\n"
        "ВАЖНО: сделай это ДО cleanup — после удаления файлов/директории эти проверки "
        "станут невозможны. Потом дай финальный ответ."
    )


# ── Cleanup Barrier ─────────────────────────────────────────────

_DELETE_RE = re.compile(r"remove-item|rmdir|(?:^|\s)rd\s|rm\s+-\w*[rf]|del\s+/s|(?:^|\s)del\s", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\"'|<>\r\n]+|(?:/[\w.\-]+){2,}")


def _deletion_target(tool_name: str, args: dict) -> str:
    """The absolute path a call would DELETE, or '' if it's not a deletion. ssh_not_exists
    is the cleanup VERIFIER (runs after delete), not the delete itself."""
    a = args or {}
    if tool_name == "ssh_run_ps":
        text = str(a.get("script") or "")
    elif tool_name in ("ssh_run", "run_bash"):
        text = str(a.get("command") or "")
    else:
        return ""
    if not text or not _DELETE_RE.search(text):
        return ""
    m = _ABS_PATH_RE.search(text)
    return m.group(0).strip().strip("\"'") if m else ""


def _norm_path(p: str) -> str:
    return p.replace("/", "\\").rstrip("\\").lower()


def _open_criteria_under(tracker: CriteriaTracker, path: str) -> list[dict]:
    """Unconfirmed NON-cleanup criteria whose path is AT or UNDER `path` — the ones a
    deletion of `path` would make permanently unverifiable."""
    root = _norm_path(path)
    out = []
    for it in tracker.items:
        if it["status"] == "confirmed" or it["intent"] == "file_not_exists":
            continue
        cp = _norm_path(_criterion_path(it))
        if cp and (cp == root or cp.startswith(root + "\\")):
            out.append(it)
    return out


def cleanup_barrier_violation(tracker: CriteriaTracker, tool_name: str, args: dict) -> str | None:
    """If this call DELETES a path while unconfirmed non-cleanup criteria live under it
    (they'd become unverifiable after deletion), return a redirect telling the model to
    verify those FIRST; else None."""
    deleted = _deletion_target(tool_name, args)
    if not deleted:
        return None
    blocked = _open_criteria_under(tracker, deleted)
    if not blocked:
        return None
    acts = missing_verifier_actions(CriteriaTracker(items=blocked))
    calls = "\n".join(f"- {a['call']}  ← {a['why']}" for a in acts) or \
        "\n".join(f"- {b['text']}" for b in blocked)
    return (
        f"СТОП: не удаляй `{deleted}` сейчас — сначала закрой критерии, которые после "
        "удаления станут непроверяемыми:\n"
        f"{calls}\n"
        "Сначала эти verifier-вызовы, потом cleanup."
    )


# ── Deterministic Final Report ──────────────────────────────────

# Model-written section headers whose CONTENT the runtime owns — dropped so the
# model can't print its own "Completion status / Unverified: nothing / all passed".
_STATUS_HEADER_RE = re.compile(
    r"^\s{0,3}#{1,6}\s*(?:completion[\s_]?status|итоговый\s+статус|статус\s+задачи|готовность"
    r"|completion|unverified|not\s+verified|failed|провален|не\s*подтвержд"
    r"|verifier\s+checks?|checks?\s+summary|итог)\b",
    re.IGNORECASE,
)
_MD_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def strip_model_status_sections(text: str) -> str:
    """Drop model-written status/unverified/failed sections (by markdown header) — the
    final status is runtime-owned. The model's action descriptions are kept."""
    if not text:
        return text
    out, skipping = [], False
    for ln in text.splitlines():
        if _MD_HEADER_RE.match(ln):
            skipping = bool(_STATUS_HEADER_RE.match(ln))
        if not skipping:
            out.append(ln)
    return "\n".join(out).rstrip()


def runtime_final_report(tracker: CriteriaTracker) -> str:
    """The deterministic status block — from criteria state, never the model's word."""
    items = tracker.items
    if not items:
        return ""
    total = len(items)
    confirmed = sum(1 for it in items if it["status"] == "confirmed")
    failed = [it for it in items if it["status"] == "failed"]
    unconf = [it for it in items if it["status"] == "unconfirmed"]
    lines = [
        "[Готовность задачи — по verifier'у (runtime, не по словам модели)]",
        f"Статус: {tracker.completion_status()} · подтверждено {confirmed}/{total}",
    ]
    if failed:
        lines.append("Провалено (verifier red): " + "; ".join(it["text"] for it in failed))
    if unconf:
        lines.append("Не подтверждено verifier'ом: " + "; ".join(it["text"] for it in unconf))
    if not failed and not unconf:
        lines.append("Все критерии подтверждены verifier'ом.")
    return "\n".join(lines)
