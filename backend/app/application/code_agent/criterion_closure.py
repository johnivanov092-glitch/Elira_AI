"""Deterministic Criterion Closure + Cleanup Barrier + Final Report (Ph7.12).

A TaskSpec run is a small state machine: work → CLOSE the open criteria with the
EXACT verifier calls → cleanup → final. This module reads the CriteriaTracker and
computes, deterministically, which verifier calls are still MISSING for criteria
whose verifier is unambiguous — so the loop can spend ONE bounded closure turn asking
for those exact calls instead of letting the model finalize a partial run while
claiming success. For the SAFE, fully-concrete subset it also emits an executable
`auto` spec so the runtime runs the verifier ITSELF (auto-verifier pass in agent_loop)
instead of hoping the model guesses the right call. It also owns the FINAL status
block (runtime-generated, never the model's word) and the CLEANUP barrier (don't
delete a path while criteria that live under it are still open). Pure: it never
executes tools; it returns text/data/specs.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePath

from app.application.code_agent.taskspec import (
    CriteriaTracker,
    _PKG_MGRS,
    _QUOTED_RE,
    _base,
    _clean_run,
    _file_tokens,
    _path_tokens_from_text,
    _run_bash_verdict,
    _run_target_and_args,
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
    verifier is not unambiguous (generic) — those stay honestly unverified rather than
    being demanded of the model."""
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
    elif intent == "behavior_test":
        return {
            "tool": "run_bash",
            "call": (
                "запусти существующий focused test suite в verbose-режиме: каждый "
                "заявленный сценарий должен иметь отдельный именованный PASSED-тест"
            ),
            "why": text,
        }
    elif intent == "viewport_layout":
        # Render at the width the criterion names (mobile for narrow/ambiguous, desktop for
        # wide) and let the browser MEASURE horizontal overflow — a real layout verdict, not
        # a grep. `viewport="mobile"` also confirms a width-agnostic layout criterion.
        preset = "desktop" if item.get("viewport_width") == "wide" else "mobile"
        return {"tool": "browser",
                "call": (f'browser(url={url}, viewport="{preset}") — отрисуй на этой ширине; '
                         f"не должно быть горизонтального переполнения (измеряется реально, не grep)"),
                "why": text}
    return None  # generic → no deterministic verifier


# ── Auto-verifier closure (runtime-owned execution) ─────────────
# The subset of missing-verifier calls the RUNTIME may execute ITSELF instead of asking
# the model (the model then only sees the result): read-only(-ish) probes whose args are
# fully concrete. Everything else stays model-directed by design — browser interactions
# (field selectors unknown at criterion level), run_server (side-effectful), remote
# cleanup ssh_not_exists (delete-then-verify is the model's flow), and kind-INFERRED
# commands (a guessed `npm run typecheck` in the wrong project would record a false red).
# Execution itself lives in agent_loop; this module stays pure (specs + text only).
_AUTO_SAFE_TOOLS = frozenset({
    "path_exists", "ssh_exists", "ssh_assert_contains", "ssh_assert_not_contains",
    "browser", "run_bash",
})


def _auto_spec(item: dict, *, host: str, url: str, allow_ssh: bool = True) -> dict | None:
    """Executable {'tool','args'} closing this criterion, or None when the call is not
    fully concrete (placeholder host/url, unknown selectors) or not in the safe set.
    `allow_ssh=False` disables remote probes — the loop passes it when the run touched
    more than one ssh host (a probe against the WRONG host could record a false red)."""
    intent = item["intent"]
    host_ok = allow_ssh and bool(host) and host != _HOST_PLACEHOLDER
    url_ok = bool(url) and url.startswith("http")
    if intent in ("content_contains", "content_not_contains"):
        path, pat = _criterion_path(item), _content_pattern(item)
        # remote-looking paths only: a LOCAL file criterion in a run that also used ssh
        # must not be asserted against a remote mirror (stale copy → false red).
        if path and pat and host_ok and not _looks_local_path(path):
            tool = "ssh_assert_contains" if intent == "content_contains" else "ssh_assert_not_contains"
            return {"tool": tool, "args": {"host": host, "path": path, "pattern": pat}}
        return None
    if intent in ("file_exists", "file_not_exists"):
        path = _criterion_path(item)
        if path and _looks_local_path(path):
            # path_exists is a PASSIVE probe (presence confirms exists; absence confirms
            # not_exists; the mismatching state is NEUTRAL) — an auto-probe can never
            # wrongly FAIL a criterion, only confirm or leave it open. Probed EXACTLY as
            # the criterion names it — no basename redirect (a same-named file elsewhere
            # must not certify a path that doesn't exist as written).
            return {"tool": "path_exists", "args": {"path": path}}
        if path and intent == "file_exists" and host_ok:
            return {"tool": "ssh_exists", "args": {"host": host, "path": path}}
        return None   # remote cleanup (ssh_not_exists) stays model-owned: delete → verify
    if intent == "dom_contains" and not item.get("interaction"):
        return {"tool": "browser", "args": {"url": url}} if url_ok else None
    if intent == "page_open":
        return {"tool": "browser", "args": {"url": url}} if url_ok else None
    if intent == "viewport_layout":
        preset = "desktop" if item.get("viewport_width") == "wide" else "mobile"
        return {"tool": "browser", "args": {"url": url, "viewport": preset}} if url_ok else None
    if intent == "command_output":
        cmd = item.get("command") or ""
        # only a command NAMED in the criterion (runnable quote) — never a kind-inferred
        # guess. allow_cd: a red command_output is NEUTRAL (can't false-fail), so the
        # deterministic `cd <dir> &&` cwd inference is safe here.
        return {"tool": "run_bash", "args": {"command": cmd}, "allow_cd": True} if cmd else None
    if intent == "command_check":
        cmd = item.get("command") or ""
        # a red command_check HARD-fails the criterion, so the bar is higher: the named
        # command must itself carry a recognizable check kind (its verdict can actually
        # close a command_check — `node server.js` can't and would just burn a slot or
        # hang the gate), and NO cwd inference (a wrong cwd would record a false red).
        if cmd and _run_bash_verdict(cmd) is not None:
            return {"tool": "run_bash", "args": {"command": cmd}, "allow_cd": False}
        return None
    return None


def resolve_auto_command(command: str, project_root, touched_files) -> str | None:
    """Make a criterion-NAMED command runnable for the auto pass: as-is when its script
    target resolves from the project root; with a deterministic `cd <dir> && ` prefix
    when the target was created in exactly ONE directory this run (`node index.js
    sample.log` after writing `log-summarizer/index.js`); None when the target can't be
    located — the model keeps the wheel rather than the runtime recording a false red
    from a wrong cwd. A pkg-manager script (`npm test`) runs as-is: its target is a
    script NAME, not a file — a filesystem precheck would wrongly reject it."""
    toks = _clean_run(command).split()
    if toks and toks[0].lower() in _PKG_MGRS:
        return command   # pkg script → project root is the right cwd by definition
    target, _args = _run_target_and_args(command)
    if not target:
        return None
    raw = next((t for t in toks if _base(t) == target), "")
    try:
        if raw and (Path(project_root) / raw).exists():
            return command
    except OSError:
        return None
    dirs = {PurePath(str(p).replace("\\", "/")).parent.as_posix()
            for p in (touched_files or ()) if _base(str(p)) == target}
    if len(dirs) == 1:
        d = dirs.pop()
        return command if d in ("", ".") else f"cd {d} && {command}"
    return None


def auto_close_summary(results: list[dict]) -> str:
    """Short runtime report of the auto-verifier pass for the model's closure turn.
    Each result: {'label', 'green', 'red', 'evidence'} — classified by the LOOP from
    actual criterion TRANSITIONS (confirmed → green; failed / ran-red-without-closing →
    red), NOT from the tool's ok flag (run_bash deliberately has no `ok`, and a
    passive probe's ok=False can itself CONFIRM a cleanup criterion). Green = don't
    redo it; red = fix the cause, then re-run exactly that check. Neutral no-ops are
    omitted (their criteria stay in the missing list naturally)."""
    green = [r for r in results if r.get("green")]
    red = [r for r in results if r.get("red")]
    lines = []
    if green:
        lines.append("Runtime уже ВЫПОЛНИЛ проверки и подтвердил критерии — НЕ повторяй их: "
                     + "; ".join(r["label"] for r in green) + ".")
    for r in red:
        ev = " ".join((r.get("evidence") or "").split())[:300]
        lines.append(f"Runtime выполнил {r['label']} — проверка НЕ прошла"
                     + (f" (evidence: {ev})" if ev else "")
                     + ". Исправь причину и перезапусти именно эту проверку.")
    return "\n".join(lines)


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
        exps = sorted({it.get("output_expected", "") for it in its
                       if it.get("output_expected") and not it.get("output_absent")})
        absents = sorted({it.get("output_expected", "") for it in its
                          if it.get("output_expected") and it.get("output_absent")})
        show = ", ".join(f"`{e}`" for e in exps) if exps else ""
        if absents:
            show += ("; " if show else "") + "НЕ должно быть " + ", ".join(f"`{e}`" for e in absents)
        show = show or "нужный вывод"
        neg = any(it.get("expect_nonzero") for it in its)
        cmd_show = cmd or "команду из задачи"
        act = {
            "tool": "run_bash",
            "call": (f"run_bash(`{cmd_show}`) — ОДИН запуск; в stdout/stderr должно быть {show}"
                     + ("; и НЕнулевой код выхода" if neg else "")
                     + " (grep/read_file НЕ доказывают вывод команды)."),
            "why": "; ".join(it["text"] for it in its)[:200],
        }
        if cmd:   # a NAMED command is runtime-executable (auto-verifier pass); a red
            # command_output is neutral, so cwd inference (cd-prefix) is allowed.
            act["auto"] = {"tool": "run_bash", "args": {"command": cmd}, "allow_cd": True}
        out.append(act)
    return out


def missing_verifier_actions(tracker: CriteriaTracker, *, host: str = _HOST_PLACEHOLDER,
                             url: str = _URL_PLACEHOLDER, auto_ssh: bool = True,
                             catalog_hints: bool = False) -> list[dict]:
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
    grouped_i = _interaction_group_actions(interaction, url)
    grouped_c = _command_group_actions(cmd_output)
    for _g in grouped_i:
        _g["intent"] = "dom_contains"    # for catalog-hint lookup (inert extra key)
    for _g in grouped_c:
        _g["intent"] = "command_output"
    grouped = grouped_i + grouped_c
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
        act["intent"] = it["intent"]
        auto = _auto_spec(it, host=host, url=url, allow_ssh=auto_ssh)
        if auto:   # runtime can execute this one itself (auto-verifier pass)
            act["auto"] = auto
        seen.add(act["call"])
        out.append(act)
    if catalog_hints:
        # R1: single-sourced negative rule from the catalog appended to each hint —
        # what this verifier does NOT accept ("grep не закрывает…"). Flag-gated: the
        # nudge text is bit-identical when catalog_assist is off. Fail-open (F9).
        try:
            from app.application.code_agent import catalog as _catalog
            for a in out:
                note = _catalog.intent_note(str(a.get("intent") or ""))
                if note:
                    a["call"] += f" [каталог: НЕ закрывает — {note}]"
        except Exception:
            pass
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
