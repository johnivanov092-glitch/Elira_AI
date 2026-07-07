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

from app.application.code_agent.taskspec import (
    CriteriaTracker,
    _QUOTED_RE,
    _file_tokens,
    _path_tokens_from_text,
    interaction_spec,
)

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
    """The file/dir path a criterion is about — a quoted path token (incl. a bare dir
    like `subnet-helper`), else the longest path in item['files']."""
    quoted_paths = [q.strip() for q in _QUOTED_RE.findall(item.get("text") or "") if _path_tokens_from_text(q)]
    if quoted_paths:
        return quoted_paths[0]
    paths = [f for f in (item.get("files") or ()) if "/" in f or "\\" in f]
    pool = paths or list(item.get("files") or ())
    return max(pool, key=len) if pool else ""


def _looks_local_path(path: str) -> bool:
    """A relative path (no Windows drive, no POSIX-absolute) is local project FS →
    verified with path_exists; an absolute/remote path uses ssh_exists on the host."""
    p = (path or "").strip()
    return not (re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("/"))


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
        if path and _looks_local_path(path):
            return {"tool": "path_exists", "call": f"path_exists(path=`{path}`)", "why": text}
        if path:
            return {"tool": "ssh_exists", "call": f"ssh_exists(host={host}, path=`{path}`)", "why": text}
    elif intent == "file_not_exists":
        path = _criterion_path(item)
        if path and _looks_local_path(path):
            return {"tool": "path_exists", "call": f"path_exists(path=`{path}`) — должен отсутствовать", "why": text}
        if path:
            return {"tool": "ssh_not_exists", "call": f"ssh_not_exists(host={host}, path=`{path}`)", "why": text}
    elif intent == "dom_contains":
        toks = item.get("targets") or set()
        show = ", ".join(f"`{t}`" for t in sorted(toks)) if toks else "нужный текст"
        if item.get("interaction"):
            return {"tool": "browser",
                    "call": (f"browser(url={url}, actions=[…fill поля, click кнопки…]) — выполни "
                             f"ввод и клик, затем в DOM должно быть {show} (НЕ grep/не node-скрипт)"),
                    "why": text}
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


def _interaction_group_actions(items: list[dict], url: str) -> list[dict]:
    """GROUP open interaction criteria by (fill value, click target) into ONE concrete
    browser(actions=…) call per group — Network/Mask/Hosts after the SAME fill+click
    close in a single call. The call carries the exact value + button + all expected
    result tokens, and tells the model NOT to restart the server (the live 10/13 spun
    into a run_server loop instead of doing this one call)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        spec = interaction_spec(it["text"])
        groups.setdefault((spec["fill"], spec["click"]), []).append(it)
    out = []
    for (fill, click), its in groups.items():
        toks = sorted({t for it in its for t in (it.get("targets") or set())})
        show = ", ".join(f"`{t}`" for t in toks) if toks else "нужный текст"
        fill_step = f'{{"fill":"<поле ввода>","value":"{fill}"}}' if fill else '{"fill":"<поле>","value":"<значение>"}'
        click_step = f'{{"click":"{click}"}}' if click else '{"click":"<кнопка>"}'
        out.append({
            "tool": "browser",
            "call": (f"browser(url={url}, actions=[{fill_step},…,{click_step}]) — ОДИН вызов "
                     f"закрывает всё это; собери шаги из критерия (fill/select/check по надобности) "
                     f"и заверши click; в DOM после действий должно быть {show}; "
                     f"НЕ перезапускай сервер (он уже поднят), НЕ используй grep/node."),
            "why": "; ".join(it["text"] for it in its)[:200],
        })
    return out


def _command_group_actions(items: list[dict]) -> list[dict]:
    """GROUP open command_output criteria by command into ONE run_bash action — one run
    closes INFO/WARN/ERROR/TOTAL for the same command (don't re-run it 5-10×). Carries
    the exact command + all expected output tokens + a non-zero-exit note when needed."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("command", ""), []).append(it)
    out = []
    for cmd, its in groups.items():
        exps = sorted({it.get("output_expected", "") for it in its if it.get("output_expected")})
        show = ", ".join(f"`{e}`" for e in exps) if exps else "нужный вывод"
        neg = any(it.get("expect_nonzero") for it in its)
        cmd_show = cmd or "команду из задачи"
        out.append({
            "tool": "run_bash",
            "call": (f"run_bash(`{cmd_show}`) — ОДИН запуск; в stdout/stderr должно быть {show}"
                     + ("; и НЕнулевой код выхода" if neg else "")
                     + " (grep/read_file НЕ доказывают вывод команды)."),
            "why": "; ".join(it["text"] for it in its)[:200],
        })
    return out


def missing_verifier_actions(tracker: CriteriaTracker, *, host: str = _HOST_PLACEHOLDER,
                             url: str = _URL_PLACEHOLDER) -> list[dict]:
    """Concrete verifier calls still missing for each unconfirmed/failed criterion whose
    verifier is unambiguous. Criteria with no deterministic verifier are omitted.

    MINIMAL plan (tool-economy): interaction criteria sharing one fill+click GROUP into a
    single browser(actions=…) call; command_output criteria sharing one command GROUP into
    a single run_bash call; a browser render proves page_open too, so we never also demand
    an http_api page_open. Duplicate calls collapse."""
    open_items = [it for it in tracker.items if it["status"] != "confirmed"]
    interaction = [it for it in open_items if it["intent"] == "dom_contains" and it.get("interaction")]
    cmd_output = [it for it in open_items if it["intent"] == "command_output"]
    rest = [it for it in open_items
            if not (it["intent"] == "dom_contains" and it.get("interaction")) and it["intent"] != "command_output"]
    out, seen = [], set()
    grouped = _interaction_group_actions(interaction, url) + _command_group_actions(cmd_output)
    # a browser DOM verifier (grouped or plain) subsumes page_open → don't also ask http_api
    has_browser_dom = bool(interaction) or any(it["intent"] == "dom_contains" for it in rest)
    for a in grouped:
        if a["call"] not in seen:
            seen.add(a["call"])
            out.append(a)
    for it in rest:
        act = _action_for(it, host=host, url=url)
        if act is None:
            continue
        if has_browser_dom and it["intent"] == "page_open":
            continue
        if act["call"] in seen:
            continue
        seen.add(act["call"])
        out.append(act)
    return out


def browser_interaction_redirect(tracker: CriteriaTracker, url: str) -> str | None:
    """When a server is ALREADY up and the only open work is browser interaction, a
    run_server restart is the wrong next step — return the exact grouped browser call(s)
    to redirect the model there instead of letting it loop on run_server."""
    open_inter = [it for it in tracker.items
                  if it["status"] != "confirmed" and it["intent"] == "dom_contains" and it.get("interaction")]
    if not open_inter:
        return None
    calls = "\n".join(f"- {a['call']}" for a in _interaction_group_actions(open_inter, url))
    return (
        "Сервер уже запущен (actual_url известен) — НЕ перезапускай его через run_server. "
        "Оставшиеся критерии закрываются интеракцией в браузере, вот точный вызов:\n"
        f"{calls}"
    )


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


# Model-authored COUNT claims about criteria/verifier-checks in the final prose
# (e.g. "17 verifier criteria — all passed", "Verifier checks: 17/17", "проверено 17
# критериев"). The ONLY authority on how many criteria exist / passed is the runtime
# block, so a model-written number is dropped — it can't be allowed to print "17"
# beside runtime "подтверждено 19/19". A number NOT tied to criteria/verifier/checks
# vocabulary (e.g. "17 files", pytest "17 passed") is left untouched.
_CRIT_NOUN = (
    r"(?:verifier(?:['’\s-]*(?:criteri\w+|checks?|провер\w+))?"
    r"|criteri\w+|критери\w+|checks?|проверк\w+|проверок)"
)
# "<n> [/m | of m | из m] <noun>", with optional leading all/все — drop the count.
_COUNT_BEFORE_RE = re.compile(
    r"(?<![\w./])(?:all\s+|все\s+)?\d+(?:\s*/\s*\d+|\s+(?:of|из|out\s+of)\s+\d+)?\s+(" + _CRIT_NOUN + r")",
    re.IGNORECASE,
)
# "<noun>: n/m" — drop the trailing count (require a slash so we don't eat prose).
_COUNT_AFTER_RE = re.compile(r"(" + _CRIT_NOUN + r")\s*[:=]?\s*\d+\s*/\s*\d+", re.IGNORECASE)


def scrub_manual_criteria_counts(text: str) -> str:
    """Strip model-authored criteria/verifier-check COUNT claims, leaving the noun so the
    sentence still reads. The count lives ONLY in the runtime block, so the model's word
    can never contradict it. Runs regardless of completion status (a confirmed run can
    still misreport the number)."""
    if not text:
        return text
    text = _COUNT_BEFORE_RE.sub(r"\1", text)
    text = _COUNT_AFTER_RE.sub(r"\1", text)
    return text


# Model success glyphs (✅/✔/✓/☑/🟢 circle/🟩 square/👍, with optional VS16) — neutralised
# to ▫ when the run is NOT confirmed, so a model checkmark can't make a partial run LOOK done
# beside the runtime status block. The deterministic panel is the only source of "готово".
# NOTE: only "pass/done" glyphs are here — the red 🟥 / yellow 🟨 squares are deliberately
# NOT scrubbed (they mean fail/partial, which are honest signals worth preserving).
_SUCCESS_MARK_RE = re.compile("[✅✔✓☑\U0001F7E2\U0001F7E9\U0001F44D]️?")


def scrub_success_marks(text: str) -> str:
    """Replace the model's success/✅ marks with a neutral ▫ — call ONLY when
    completion_status != confirmed. A ✅ next to a criterion/interaction row on a partial
    run is misleading; the runtime block + readiness panel own the verdict."""
    if not text:
        return text
    return _SUCCESS_MARK_RE.sub("▫", text)


def report_counts(report: list[dict]) -> dict[str, int]:
    """The counts, computed ONCE from criteria.report() — the single source the final
    block agrees with. `skipped` = conditional criteria that were n/a."""
    return {
        "total": len(report),
        "confirmed": sum(1 for it in report if it["status"] == "confirmed"),
        "failed": sum(1 for it in report if it["status"] == "failed"),
        "unconfirmed": sum(1 for it in report if it["status"] == "unconfirmed"),
        "skipped": sum(1 for it in report if it["status"] == "skipped"),
    }


def runtime_final_report(tracker: CriteriaTracker) -> str:
    """The deterministic status block — built ONLY from criteria.report(), never the
    model's word. Counts come from report_counts() so they're computed one time."""
    report = tracker.report()
    if not report:
        return ""
    c = report_counts(report)
    failed = [it for it in report if it["status"] == "failed"]
    unconf = [it for it in report if it["status"] == "unconfirmed"]
    skipped = [it for it in report if it["status"] == "skipped"]
    # confirmed out of the MANDATORY total (skipped conditionals excluded from the denom)
    mandatory = c["total"] - c["skipped"]
    head = f"Статус: {tracker.completion_status()} · подтверждено {c['confirmed']}/{mandatory}"
    if c["skipped"]:
        head += f" (+{c['skipped']} n/a)"
    lines = [
        "[Готовность задачи — по verifier'у (runtime, не по словам модели)]",
        head,
    ]
    if failed:
        lines.append("Провалено (verifier red): " + "; ".join(it["text"] for it in failed))
    if unconf:
        lines.append("Не подтверждено verifier'ом: " + "; ".join(it["text"] for it in unconf))
    if skipped:
        lines.append("Пропущено (условные, n/a): " + "; ".join(it["text"] for it in skipped))
    if not failed and not unconf:
        lines.append("Все обязательные критерии подтверждены verifier'ом.")
    return "\n".join(lines)
