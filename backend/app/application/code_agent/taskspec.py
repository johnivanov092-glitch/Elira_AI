"""TaskSpec — the goal/criteria layer above a run (runtime plan Phase 6).

A run should know WHAT DONE MEANS, not just "the user typed something". A TaskSpec
carries the goal, the explicit success criteria, and the verifiers that PROVE each
criterion — so completion is decided by a verifier passing, not by the model
asserting "готово". The loop injects it into the task context and gates finalize
on it (softly), and the deterministic report closes against it.

Derivation here is a conservative 0-token HEURISTIC: it fires ONLY when the task
is clearly structured (a "Цель/Goal" header, bulleted/numbered criteria, or hard
artifacts like ports / test scripts). A one-line "почини баг X" yields None — no
spec, no gate, no injected tokens — so simple runs and the prompt canaries are
untouched. An LLM pre-pass can later replace/augment `derive_task_spec` without
touching the rest of the layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TaskSpec:
    goal: str = ""
    scope: list[str] = field(default_factory=list)            # files/paths in play
    constraints: list[str] = field(default_factory=list)      # what must NOT happen
    success_criteria: list[str] = field(default_factory=list)  # how DONE is proven
    verifiers: list[str] = field(default_factory=list)        # concrete checks to run
    stop_conditions: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)          # spec/behaviour — NOT criteria


# Section headers (case-insensitive, exact match on the pre-colon token).
_HEADERS: dict[str, tuple[str, ...]] = {
    "goal": ("цель", "goal", "задача", "task", "objective"),
    "constraints": (
        "ограничения", "constraints", "нельзя", "запрещено",
        "подвох", "подвохи", "важно", "guardrails", "проверка логики",
        "проверка рантайма", "логика проверки", "tool economy",
        "экономия инструментов", "правила", "notes", "заметки",
    ),
    "criteria": ("критерии", "критерии готовности", "success criteria", "проверить",
                 "definition of done", "готовность", "acceptance", "checks"),
    # SPEC/behaviour sections — describe WHAT to build, NOT how DONE is proven. Their
    # bullets ("Поле ввода с label CIDR", "После нажатия Calculate показать …") are
    # implementation detail, not verifiable readiness criteria, so they are routed to
    # `details` and never inflate success_criteria (Subnet Helper: 10 such lines leaked).
    "details": ("функциональность", "функционал", "программа", "описание",
                "функциональные требования", "спецификация", "specification", "spec",
                "features", "поведение", "что делает", "что должно делать", "фичи"),
    "stop": ("условия остановки", "stop conditions", "стоп-условия"),
    # Report-only sections: their items describe WHAT TO REPORT, not verifiable
    # success criteria (FIX-9 #3), so they are routed away from criteria. Header
    # detection only fires on a `<phrase>:` line, so bare "Отчёт:" is safe here.
    "report": ("финальный отчёт", "финальный отчет", "отчёт", "отчет", "report"),
}
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)")
_PORT_RE = re.compile(r"(?:порт|port)\s*[:#№]?\s*(\d{2,5})", re.IGNORECASE)
# Test scripts/commands only — a name must actually look like a test (contains
# "test"), so the script UNDER test (e.g. agent-lab.ps1) isn't taken for a verifier.
_TEST_RE = re.compile(
    r"\b(pytest|npm\s+test|go\s+test|[\w.\-]*test[\w.\-]*\.(?:ps1|py|sh|js|ts)|test\.(?:ps1|py|sh|js|ts))\b",
    re.IGNORECASE,
)
# Cues that a line states a real criterion (so a stray sentence isn't mistaken).
_CRITERIA_CUES = (
    "должен", "должно", "должна", "должны", "проверь", "убедись", "чтобы",
    "пройти", "пройдёт", "listening", "слушает", "успешно", "стартует",
    "работает", "не содержит", "содержит", "удал", "8/8", "200 ok", "exit 0",
    "must ", "should ", "verify", "assert",
)


# Continuation signals — the ONLY case where a TaskSpec is restored from history
# (FIX-8). "делай"/"продолжай" anywhere = continue the prior task; short
# affirmatives (да/ок/go) only when the message is short. A NEW question after a
# structured task ("привет", "объясни X") must NOT drag the old criteria back.
_STRONG_CONTINUE_CUES = frozenset({
    "делай", "продолжай", "продолжи", "продолжайте", "продолжаем", "доделай",
    "доведи", "заверши", "закончи", "дальше", "далее",
    "continue", "proceed", "finish", "resume",
})
_SHORT_AFFIRM_CUES = frozenset({
    "да", "ок", "окей", "ага", "давай", "поехали", "го", "погнали", "ладно",
    "yes", "ok", "okay", "go", "next",
})


def is_continuation_message(msg: str | None) -> bool:
    """True when `msg` asks to CONTINUE the prior task — the gate for restoring a
    TaskSpec from history (FIX-8). Conservative: a strong continue-imperative
    (делай/продолжай) anywhere, or a short (≤4-word) affirmative (да/ок/go)."""
    words = re.sub(r"[^\w\s]", " ", (msg or "").lower()).split()
    if not words:
        return False
    if any(w in _STRONG_CONTINUE_CUES for w in words):
        return True
    return len(words) <= 4 and any(w in _SHORT_AFFIRM_CUES for w in words)


def _match_header(token: str) -> str | None:
    # Match the exact header, its LAST word, or a phrase that BEGINS with a known
    # header — so "После запуска проверить:" (last word), "Критерии готовности
    # frontend:" / "Критерии готовности SSH/home-srv01:" (prefix) are all recognised,
    # not only the bare "Критерии готовности:". Fires only on a non-bullet `<phrase>:`
    # line (the caller guards that), so a plain sentence is never a header.
    low = token.strip().lower().rstrip(":").strip()
    words = low.split()
    last = words[-1] if words else ""
    for section, keys in _HEADERS.items():
        if low in keys or (last and last in keys):
            return section
        if any(low.startswith(k + " ") for k in keys):   # "<known header> <suffix>:"
            return section
    return None


def _looks_like_criterion(line: str) -> bool:
    low = line.lower()
    return any(cue in low for cue in _CRITERIA_CUES)


def derive_task_spec(task_text: str | None, project_root=None) -> TaskSpec | None:
    """Heuristic TaskSpec, or None when the task isn't structured enough to bother
    (simple/conversational). Never raises; conservative on purpose. `project_root`
    (optional) enriches an already-firing spec with project verifiers (.elira/verify,
    package.json typecheck/build) — it never turns a simple task into a spec."""
    text = (task_text or "").strip()
    if not text:
        return None

    goal_lines: list[str] = []
    criteria: list[str] = []
    constraints: list[str] = []
    stop: list[str] = []
    details: list[str] = []
    section = "goal"
    saw_goal_header = False

    # An EXPLICIT "Критерии готовности" header means the criteria are enumerated in
    # their own section — so free-floating bullets under the goal / an unrecognised
    # section must NOT be scooped up as criteria too (Subnet Helper: "Функциональность"
    # bullets became criteria only because there was no header above them).
    has_explicit_criteria = any(
        (not _BULLET_RE.match(raw)) and ":" in (ln := raw.strip())
        and _match_header(ln.partition(":")[0]) == "criteria"
        for raw in text.splitlines()
    )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        bullet = _BULLET_RE.match(raw)
        # A section HEADER is a NON-bullet `<phrase>:` line. A bullet is CONTENT even
        # when it contains a colon — "- content criteria … verifier checks:
        # `ssh_assert_contains`" must NOT be read as a 'checks' header that flips the
        # section and dumps a Подвох rule in as a success criterion.
        if not bullet and ":" in line:
            head, _, rest = line.partition(":")
            sec = _match_header(head)
            if sec is not None:
                section = sec
                if sec == "goal":
                    saw_goal_header = True
                rest = rest.strip()
                if rest:
                    _route(section, rest, goal_lines, criteria, constraints, stop, details)
                continue
        # A bullet under the goal (no section header yet) is a criterion ONLY when the
        # task has no explicit criteria section; otherwise it's spec/detail, not a gate.
        goal_bullet_target = "details" if has_explicit_criteria else "criteria"
        if bullet:
            _route(section if section != "goal" else goal_bullet_target,
                   bullet.group(1).strip(), goal_lines, criteria, constraints, stop, details)
            continue
        _route(section, line, goal_lines, criteria, constraints, stop, details)

    goal = " ".join(goal_lines).strip()[:300]

    # Artifacts → concrete verifiers (strong signals only; a bare file mention is
    # NOT enough to conjure a verifier or a spec).
    verifiers: list[str] = []
    seen_ports: set[str] = set()
    for m in _PORT_RE.finditer(text):
        p = m.group(1)
        if p not in seen_ports:
            seen_ports.add(p)
            verifiers.append(f"ssh_port_check порт {p} → LISTENING")
    seen_tests: set[str] = set()
    for m in _TEST_RE.finditer(text):
        t = m.group(1)
        if t.lower() not in seen_tests:
            seen_tests.add(t.lower())
            verifiers.append(f"прогнать {t} и убедиться, что проходит")
    # coding checks named in the task text
    low = text.lower()
    if any(k in low for k in ("typecheck", "type-check", "tsc")):
        verifiers.append("npm run typecheck → 0 ошибок типов")
    if "build" in low and any(k in low for k in ("npm", "vite", "сборк", "собира")):
        verifiers.append("npm run build → успешная сборка")
    if "pytest" in low and not any("прогнать" in v for v in verifiers):
        verifiers.append("pytest → все тесты зелёные")

    # Fire only on a genuinely structured task: explicit criteria, OR a stated goal
    # backed by a hard verifier. Otherwise None (no spec, no gate, no tokens).
    if not criteria and not (saw_goal_header and verifiers):
        return None

    # Enrich an already-firing spec with project-derived verifiers (never a trigger).
    if project_root is not None:
        verifiers += _project_verifiers(project_root)

    # A criterion that enumerates several required strings ("file contains lines: A,
    # B, C") becomes one criterion PER string, so each is verified independently and
    # earns partial credit — instead of one all-or-nothing criterion (FIX #4).
    criteria = [part for c in criteria for part in _split_multi_target(c)]

    return TaskSpec(
        goal=goal,
        constraints=_dedupe(constraints),
        success_criteria=_dedupe(criteria),
        verifiers=_dedupe(verifiers),
        stop_conditions=_dedupe(stop),
        details=_dedupe(details),
    )


def _project_verifiers(project_root) -> list[str]:
    """Verifiers the PROJECT itself implies — the runtime knows the checks, not just
    the task text. Best-effort; never raises."""
    out: list[str] = []
    try:
        from pathlib import Path

        root = Path(project_root)
        if (root / ".elira" / "verify").exists():
            out.append(".elira/verify → exit 0")
        for pkg in (root / "package.json", root / "frontend" / "package.json"):
            if pkg.exists():
                import json

                scripts = (json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts", {})
                if "typecheck" in scripts:
                    out.append("npm run typecheck → 0 ошибок типов")
                if "build" in scripts:
                    out.append("npm run build → успешная сборка")
                break
    except Exception:
        pass
    return out


def _route(section: str, line: str, goal, criteria, constraints, stop, details) -> None:
    if section == "goal":
        goal.append(line)
    elif section == "constraints":
        constraints.append(line)
    elif section == "stop":
        stop.append(line)
    elif section == "details":
        details.append(line)  # spec/behaviour — never a readiness criterion
    elif section == "report":
        return  # report-only requirement (FIX-9 #3) — not a verifiable criterion
    else:  # criteria
        if _criterion_intent(line) == "report":
            return  # inline "описано в отчёте / что создано" — a report requirement, not a criterion
        if _is_scope_rule(line):
            constraints.append(line)  # a scope/safety rule sitting in a criteria section
            return
        if _BULLET_RE.match(line) or _looks_like_criterion(line) or section == "criteria":
            criteria.append(line)
        else:
            goal.append(line)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def taskspec_context(spec: TaskSpec) -> str:
    """Compact task block injected into the run context (NOT the base prompt, so it
    costs nothing on unstructured runs / canaries)."""
    parts = ["[ЗАДАЧА — держи цель и критерии в фокусе весь прогон]"]
    if spec.goal:
        parts.append(f"Цель: {spec.goal}")
    if spec.details:
        parts.append("Что реализовать (описание/поведение — это НЕ критерии готовности):")
        parts.extend(f"- {d}" for d in spec.details[:12])
    if spec.success_criteria:
        parts.append("Критерии готовности (докажи verifier'ом, НЕ словами):")
        parts.extend(f"- {c}" for c in spec.success_criteria[:10])
    if spec.verifiers:
        parts.append("Чем проверять: " + "; ".join(spec.verifiers[:8]))
    if spec.constraints:
        parts.append("Ограничения: " + "; ".join(spec.constraints[:8]))
    _intents = {_criterion_intent(c) for c in spec.success_criteria}
    if _intents & {"file_exists", "file_not_exists"}:
        parts.append(
            "Существование локальных файлов/папок доказывай инструментом `path_exists` "
            "(read-only; если его нет в списке — активируй через tool_search), а не grep/read."
        )
    # Tool-economy route — only on a browser-observable (frontend/page) task, so it
    # never nudges ssh/backend runs. browser proves page_open AND the visible text in
    # one call; a bundle grep proves neither. Keeps a small frontend verify lean.
    if {_criterion_intent(c) for c in spec.success_criteria} & {"dom_contains", "page_open"}:
        parts.append(
            "Экономный маршрут проверки (небольшой фронт): typecheck → build → run_server → "
            "browser(actual_url) для DOM/интеракций → stop. browser доказывает И открытие "
            "страницы, И видимый текст — http_api для тех же DOM-критериев не нужен; grep по "
            "бандлу видимый текст НЕ доказывает. Получив actual_url, не повторяй run_server "
            "list/logs на живом сервере — иди в browser."
        )
    return "\n".join(parts)


def taskspec_report(spec: TaskSpec) -> dict:
    """Serialisable task-state for the done event / UI panel."""
    return {
        "goal": spec.goal,
        "success_criteria": spec.success_criteria[:10],
        "verifiers": spec.verifiers[:8],
    }


# ── per-criterion state (Ph7.4/7.5) ─────────────────────────────

_SALIENT_RE_NUM = re.compile(r"\d{2,5}")
# File/path targets: a Windows drive path (dirs too — `C:\AgentLabCanary` has no
# extension), a multi-segment POSIX path, or a bare filename with an extension.
# The drive/POSIX alternatives cover directory criteria; the extension form covers
# bare names like `health.txt`. Single-segment `и/или`-style prose is NOT captured.
_SALIENT_RE_FILE = re.compile(
    r"[A-Za-z]:\\[^\s`'\"<>|]+"       # C:\AgentLabCanary  or  C:\AgentLab\agent-lab.ps1
    r"|(?:/[\w.\-]+){2,}"              # /var/agent/health.txt
    r"|[\w\-]+\.\w{1,5}"              # health.txt, test.ps1
)


def _file_tokens(text: str) -> set[str]:
    """File/path targets in `text` — full path AND basename, so a criterion naming
    `agent-lab.ps1` matches a verifier on `C:\\AgentLab\\agent-lab.ps1`, and a
    directory criterion `C:\\AgentLabCanary` matches an ssh_exists on the same dir."""
    toks: set[str] = set()
    for m in _SALIENT_RE_FILE.findall(text or ""):
        m = m.rstrip(".,;:!?)»`\"'").lower()  # drop trailing prose punctuation
        if not m:
            continue
        toks.add(m)
        toks.add(re.split(r"[\\/]", m)[-1])  # basename
    return toks


# A quoted name is path-like when it has an extension, a separator, or a hyphen —
# so a bare directory `subnet-helper` (no dot/slash → missed by _SALIENT_RE_FILE) is
# still captured as a file target for a local file_exists criterion.
_QUOTED_NAME_RE = re.compile(r"^[\w][\w.\-]*(?:[\\/][\w.\-]+)*$")


def _path_tokens_from_text(text: str) -> set[str]:
    """File/dir targets a criterion is about — _file_tokens plus quoted names that look
    path-like (`subnet-helper`, `subnet-helper/package.json`). Full token + basename."""
    toks = _file_tokens(text)
    for q in _QUOTED_RE.findall(text or ""):
        q = q.strip().rstrip(".,;:!?)»").lower()
        if q and _QUOTED_NAME_RE.match(q) and any(ch in q for ch in "./\\-"):
            toks.add(q)
            toks.add(re.split(r"[\\/]", q)[-1])
    return toks


def _path_tokens_from_arg(path: str) -> set[str]:
    """File/dir tokens from a raw tool path argument (`subnet-helper`,
    `subnet-helper/package.json`) — full normalised token + basename."""
    p = (path or "").strip().strip("`\"'").replace("\\", "/").rstrip("/").lower()
    if not p:
        return set()
    return {p, p.split("/")[-1]}


# ── generic verifier contract (Ph7.9) ───────────────────────────
#
# DONE is decided by INTENT + TARGET + verifier EVIDENCE, never by the task's words
# or the model's final text. The intents below are DOMAIN-AGNOSTIC — there is no
# `if "VaultDesk"` anywhere; the same set verifies a landing page, a dashboard, a
# backend smoke test, an SSH file/cleanup task, or a plain coding task:
#
#   command_check       run_bash exit 0            typecheck/build/test/import/smoke passed
#   server_started      run_server / ssh_port_check a dev/app server is up on a URL/port
#   page_open           http_api 2xx / browser      a URL actually opened (not blocked)
#   dom_contains        browser rendered DOM        the page's TEXT contains a token
#   viewport_layout     browser viewport evidence   loaded, nonblank, no h-overflow
#   file_exists         ssh_exists (present)        a path exists
#   file_not_exists     ssh_exists (absent)         a path is gone (cleanup)
#   content_contains    ssh_assert_contains         a FILE contains a pattern
#   content_not_contains ssh_assert_not_contains    a FILE does not contain a pattern
#
# The hard disambiguation: "содержит текст X" is dom_contains when the criterion is
# about the rendered page, but content_contains when it's about a file — the TARGET
# CONTEXT decides, not the verb.

_QUOTED_RE = re.compile(r"[`«\"']([^`«»\"']{1,60})[`»\"']")
_AFTER_TEXT_MARKERS = ("текст ", "надпись ", "надписи ", "text ", "label ", "заголовок ", "кнопк")
# A criterion that requires a user ACTION before the DOM shows the result.
_INTERACTION_CUES = ("нажат", "нажми", "клик", "click", "ввод", "введ", "type ", "fill",
                     "заполн", "interaction", "интеракц", "submit")
# Where the EXPECTED result begins — tokens after this are what must be on the page;
# tokens before (the input value, the button) are the action, not a DOM assertion.
_RESULT_MARKERS = ("содержит", "contains", "показать", "показыва", "появ", "appears",
                   "выводит", "отобража", "результат", "→", "->", "then ", "затем ")


def _quoted_tokens(text: str) -> set[str]:
    return {m.strip().lower() for m in _QUOTED_RE.findall(text or "") if m.strip()}


def _is_interaction(text: str) -> bool:
    return _has((text or "").lower(), _INTERACTION_CUES)


# Value-introducing cues only — NOT "в поле" (that locates the FIELD, not the value:
# "в поле `CIDR` введите `X`" — the value is after "введите", the quote after "в поле"
# is the field name).
_FILL_CUES = ("ввод", "введ", "впиш", "заполн", "type ", "fill", "набер")
_CLICK_CUES = ("нажат", "нажми", "клик", "click", "кнопк", "button", "submit")


def interaction_spec(text: str) -> dict:
    """The (fill value, click target) an interaction criterion implies — the quoted
    token right after a fill/click cue. Result tokens (the expected DOM text) are
    excluded from both, and the click target is excluded from the fill, so a prose value
    with only the button quoted ("Введите CIDR и нажмите `Calculate`") doesn't grab the
    button, and a prose button with only the result quoted doesn't grab the result.
    Empty when absent → the closure emits a `<value>`/`<кнопка>` placeholder."""
    low = (text or "").lower()
    quotes = [(m.start(), m.group(1).strip()) for m in _QUOTED_RE.finditer(text or "")]
    result_toks = {t.lower() for t in _dom_targets(text)}  # expected DOM text — never fill/click

    def _after(cues: tuple[str, ...], exclude: set[str]) -> str:
        positions = [low.find(c) for c in cues if c in low]
        if not positions:
            return ""
        pos = min(positions)
        for start, q in quotes:
            if start > pos and q and q.lower() not in exclude:
                return q
        return ""

    click = _after(_CLICK_CUES, result_toks)
    fill = _after(_FILL_CUES, result_toks | ({click.lower()} if click else set()))
    return {"fill": fill, "click": click}


# ── CLI command-output (Batch A) ─────────────────────────────────
# A criterion asserting a COMMAND prints something ("`node index.js sample.log` выводит
# `INFO: 2`"), verified by run_bash stdout/stderr — NOT a bundle grep or a code read.
_OUTPUT_CUES = ("выводит", "выведет", "печатает", "вывод", "на выходе", "в выводе",
                "в stdout", "prints", "outputs", "output", "stdout")
# Output VERBS a value follows ("выводит `X`") — distinct from location nouns
# (stdout/output/в выводе), so the expected token anchors on the verb, not a trailing
# 'в stdout' that has no quote after it (which would drop the token).
_OUTPUT_VERB_CUES = ("выводит", "выведет", "печатает", "prints", "outputs")
_NONZERO_CUES = ("завершается с ошибк", "с ошибкой", "non-zero", "ненулев", "падает",
                 "exits non-zero", "exit code 1", "код возврата", "с ненулевым", "ненулевым кодом")
_RUNNABLE_RE = re.compile(
    r"^(?:node|python3?|npm|pnpm|yarn|deno|ts-node|bash|sh|go|cargo|php|ruby|\./)\b"
    r"|\.(?:js|mjs|cjs|py|sh|ts)\b", re.IGNORECASE)


def _runnable_quote(text: str) -> str:
    """A quoted token that looks like a runnable command (`node index.js sample.log`)."""
    for q in _QUOTED_RE.findall(text or ""):
        if _RUNNABLE_RE.search(q.strip()):
            return q.strip()
    return ""


# Positive-evidence command matching (a denylist of "printers" can never be complete —
# adversarial review kept finding new ones — so instead REQUIRE that the run EXECUTES the
# named program: its script/module is the interpreter's run target).
_ENV_PREFIX_RE = re.compile(r"^(?:\s*env\s+)?(?:\s*[A-Za-z_]\w*=\S*\s+)+", re.IGNORECASE)
_SHELL_WRAP_RE = re.compile(r"""^(?:bash|sh)\s+-\w*c\s+["']?(.+?)["']?\s*$""", re.IGNORECASE)
_INTERP_RE = re.compile(r"^(?:node|python3?|deno|ts-node|tsx|ruby|php|perl|go|bash|sh)$", re.IGNORECASE)
_EVAL_FLAGS = frozenset({"-e", "--eval", "-c", "-r", "-p", "--print", "eval", "-"})  # inline eval → runs no file
_PKG_MGRS = frozenset({"npm", "pnpm", "yarn", "npx"})
# Shell composition (after the leading `cd &&`/`bash -c` are unwrapped): a chained/piped/
# redirected/substituted run mixes other commands' output, so a matched token can't be
# ATTRIBUTED to the named program (`node index.js && echo OK` — echo fabricates it). Such
# a run does not confirm command_output — run the program on its own to verify its output.
_COMPOSITE_RE = re.compile(r"&&|\|\||;|\||\$\(|`|>>|>|<")


def command_spec(text: str) -> dict:
    """(command, expected output, expect_nonzero) for a CLI-output criterion. command is
    the runnable quoted token (may be '' when the run is described in prose); the expected
    output is the quoted token after the LAST output cue (so an unrelated quote before the
    cue isn't mistaken for it); expect_nonzero when a failure cue is present."""
    low = (text or "").lower()
    command = _runnable_quote(text)
    output_expected = ""
    # Anchor on an output VERB and take the quote right after it — trying verbs in order
    # so the value after the FIRST verb wins ('выводит `2` и печатает …' → `2`, not '');
    # fall back to any output cue when there is no verb ('в stdout `X`').
    anchors = sorted(low.find(c) for c in _OUTPUT_VERB_CUES if c in low) \
        or sorted(low.find(c) for c in _OUTPUT_CUES if c in low)
    for anchor in anchors:
        for m in _QUOTED_RE.finditer(text or ""):
            q = m.group(1).strip()
            if m.start() > anchor and q and q != command:
                output_expected = q
                break
        if output_expected:
            break
    return {"command": command, "output_expected": output_expected,
            "expect_nonzero": _has(low, _NONZERO_CUES)}


def _is_command_output(text: str) -> bool:
    """A CLI-output criterion — requires an output cue, a NAMED runnable command (a
    runnable quoted token like `node index.js sample.log`), AND a concrete expected token.
    A PROSE-described run with no named command ('запуск с CSV выводит `OK`') is NOT
    verifiable this way — it stays generic (honest) rather than being confirmable by any
    run that prints the token. An output mention with no expected token → command_check."""
    low = (text or "").lower()
    if not _has(low, _OUTPUT_CUES):
        return False
    spec = command_spec(text)
    return bool(spec["command"]) and bool(spec["output_expected"])


def _strip_cd_prefix(cmd: str) -> str:
    return re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", cmd or "", flags=re.IGNORECASE).strip()


def _clean_run(cmd: str) -> str:
    """Normalise a run command for matching: strip a leading `cd x &&`, `env`/`VAR=val`
    prefix, and unwrap `bash -c "<inner>"` / `sh -c` so the inner command is judged."""
    core = _ENV_PREFIX_RE.sub("", _strip_cd_prefix(cmd or ""))
    m = _SHELL_WRAP_RE.match(core)
    if m:
        core = _ENV_PREFIX_RE.sub("", _strip_cd_prefix(m.group(1)))
    return core.strip()


def _base(tok: str) -> str:
    return re.split(r"[\\/]", tok.strip())[-1].lower()


def _run_target_and_args(cmd: str) -> tuple[str, set[str]]:
    """(executed target basename, arg basenames) — the script/module the command RUNS
    and its args. ('' , set()) when nothing verifiable is executed: an inline-eval
    one-liner (`node -e`/`python -c`) or a command with no positional program. This is
    POSITIVE evidence (the run must execute the named program) — not a denylist of
    printers, so echo/cat/sed/jq/cut/… naturally fail: they become their OWN target, not
    the criterion's script."""
    toks = _clean_run(cmd).split()
    if not toks:
        return "", set()
    head = toks[0].lower()
    if head in _PKG_MGRS:                       # npm [run] <script> → the script name is the target
        rest = [t for t in toks[1:] if t.lower() != "run" and not t.startswith("-")]
        return (_base(rest[0]), set()) if rest else ("", set())
    if _INTERP_RE.match(head):                  # interpreter → first non-flag positional is the script
        i = 1
        while i < len(toks):
            t = toks[i]
            tl = t.lower()
            if tl in _EVAL_FLAGS:               # -e/-c/-p/eval → runs a literal, no file executed
                return "", set()
            if tl == "-m":                      # module run: `python -m app`
                return (_base(toks[i + 1]), {_base(x) for x in toks[i + 2:] if not x.startswith("-")}) \
                    if i + 1 < len(toks) else ("", set())
            if t.startswith("-"):
                i += 1
                continue
            return _base(t), {_base(x) for x in toks[i + 1:] if not x.startswith("-")}
        return "", set()
    # a direct program: ./bin, script.py, or an unknown binary → it IS the target
    return _base(head), {_base(x) for x in toks[1:] if not x.startswith("-")}


def _run_invokes(criterion_cmd: str, run_cmd: str) -> bool:
    """The recorded run actually EXECUTES the program the criterion names: same run
    target (script basename / module / pkg-script) and the criterion's args are a subset
    of the run's. Interpreter/path/env/`bash -c` wrapper differences are ignored, so
    `node ./index.js sample.log` ≡ `node index.js sample.log` and `python3` ≡ `python`;
    an inline-eval (`node -e … index.js`), a text-dump (`cat index.js`), or a different
    arg does NOT — its run target is '' or a different program. A composite run
    (`node index.js && echo OK`, `… | tee`) is rejected: the token can't be attributed
    to the named program."""
    if _COMPOSITE_RE.search(_clean_run(run_cmd)):
        return False
    ct, cargs = _run_target_and_args(criterion_cmd)
    rt, rargs = _run_target_and_args(run_cmd)
    return bool(ct) and ct == rt and cargs.issubset(rargs)


# A criterion whose applicability depends on the PROJECT having something ("если в
# проекте есть npm run typecheck, он проходит") — optional, not a hard deliverable.
# Deliberately narrow: only project-/tooling-presence and explicit-optional phrasings.
# Bare "если существует"/"если доступ"/"если имеется"/"if it exists" are EXCLUDED —
# they over-match a MANDATORY existence/availability criterion casually written with a
# leading "если …" ("проверь, если существует файл X"), which would then be silently
# skipped and drop out of the mandatory denominator (review of 6a47eaa flagged this).
_CONDITIONAL_CUES = ("если в проекте", "при наличии", "if present", "if the project has",
                     "опционал", "optional", "по возможности",
                     "если есть скрипт", "если есть script", "если есть команда")


def _is_conditional(text: str) -> bool:
    return _has((text or "").lower(), _CONDITIONAL_CUES)


def _dom_targets(text: str) -> set[str]:
    """The literal strings a DOM criterion says must be on the page. Quoted/back-ticked
    tokens first (`VaultDesk`, `Start local audit`); else the phrase after a
    'текст…/text…' marker. Domain-agnostic — any task's tokens, never hardcoded.

    For an INTERACTION criterion ("после ввода `X` и нажатия `Calculate` DOM содержит
    `Network: …`") only the EXPECTED RESULT after the last result marker is required —
    the input value `X` and the button `Calculate` are the action, and demanding them
    in the body text wrongly kept every interaction criterion unconfirmed (Subnet)."""
    low = (text or "").lower()
    if _is_interaction(text):
        cut = max((low.rfind(m) for m in _RESULT_MARKERS), default=-1)
        if cut >= 0:
            res = _quoted_tokens(text[cut:])
            if res:
                return res
    toks = _quoted_tokens(text)
    if toks:
        return toks
    ll = low
    for marker in _AFTER_TEXT_MARKERS:
        i = ll.rfind(marker)
        if i >= 0:
            rest = (text or "")[i + len(marker):].strip().strip("`«»\"'.,;:()").lower()
            if rest and len(rest) <= 40:
                return {rest}
    return set()


def _has(low: str, cues: tuple[str, ...]) -> bool:
    return any(c in low for c in cues)


def _norm_dom(s: str) -> str:
    """Normalise text for DOM-token matching: lowercase; collapse every run of
    non-(word/dot) chars (colons, pipes, newlines, punctuation) to one space; and turn a
    dot that is NOT between two digits into a space too. So an IP/mask keeps its dots
    (192.168.88.0) and boundary-matches exactly, while a sentence/label period ("alerts.")
    becomes a word boundary — a label rendered without its colon still matches
    ("Network 192.168.88.0" ↔ token "Network: 192.168.88.0")."""
    low = re.sub(r"[^\w.]+", " ", (s or "").lower())
    low = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", low)   # dot not between digits → space
    return re.sub(r"\s+", " ", low).strip()


# Context cue groups — WHAT a criterion is about (a rendered page vs a file vs a
# server vs a command). These pick the intent; none of them names a product.
_NEG_CTX = ("не содержит", "не должно", "не должен", "does not", "not contain", "без строки",
            "без текста", "отсутству", "нет строки")
# DOM/rendered-page context — a criterion is about what the page SHOWS. Includes the
# UI-element nouns (секция/кнопка/pricing/hero) so "есть секция с `X`" is a visibility
# claim, verifiable only by a browser DOM render (never a bundle grep).
_DOM_CTX = ("dom", "rendered", "на первом экране", "на экране", "первом экране", "на страниц",
            "на странице", "в браузере", "визуальн", "отобража", "видно", "виден", "видна",
            "отрисов", "интерфейс", "верстк", "hero", "pricing", "cta", "секц", "преимуществ",
            "кнопк", "название", "заголов", "надпись", "карточк", "меню", "навигац", "dashboard",
            "экран", "ui ", "лендинг", "landing")
_DOM_VERB = ("содержит", "contains", "включает", "видно", "виден", "видна", "показыва",
             "отобража", "visible", "есть текст", "есть надпись", "текст ", "надпись", "есть ")
_FILE_CTX = ("файл", "file", "путь ", "path", "каталог", "директор", "папк", ".ps1", ".txt",
             ".py", ".json", ".log", ".conf", ".cfg", ".xml", ".ini")
_SERVER_CTX = ("dev server", "dev-server", "run_server", "запуска", "запущен", "поднят",
               "стартова", "server start", "started", "listening", "слушает", "порт ", "port ",
               "actual_url", "actual_port", "возвращает actual", "returns actual",
               "url доступен", "сервер запущ", "сервер работает")
_OPEN_CTX = ("открыва", "открыл", "открыть страниц", "opens", "open the", "loads", "load page",
             "загружа", "reachable", "доступна по", "доступен по", "рендерится", "без ssrf", "без блок",
             "http 200", "200 ok", "отдаёт 200", "возвращает 200", "отвечает 200", "статус 200",
             "status 200", "→ 200", "-> 200", "endpoint откр", "url откр")
_CMD_CTX = ("typecheck", "type-check", "tsc", "mypy", "pyright", "npm run", "npm test", "yarn ",
            "pnpm ", "pytest", "unittest", "jest", "vitest", "go test", "cargo ", "build", "сборк",
            "собира", "билд", "компил", "import", "smoke", "проходит провер", "проходят провер",
            "доступные провер", "exit 0", "линт", "lint", "тесты проход", "ошибок типов")
_VIEWPORT_CTX = ("viewport", "адаптив", "responsive", "mobile", "desktop", "мобильн", "десктоп",
                 "overflow", "переполн", "горизонтальн скролл", "раскладк", "layout")
# A layout criterion is only meaningfully proven at the width it names — so classify the
# intended viewport bucket from EXPLICIT device keywords only. narrow ("mobile"/"телефон")
# must be measured narrow, wide ("desktop") wide; both-named or neither → None (any measured
# width confirms). Deliberately NOT parsing "NNpx" from prose: a criterion's px values are
# usually CSS sizes (font 16px, gap 24px, card 280px), NOT a viewport width — inferring a
# bucket from them mis-routed a desktop criterion to a mobile render (Batch D review, 3 finds).
_VP_NARROW = ("мобиль", "телефон", "смартфон", "mobile", "phone")
_VP_WIDE = ("десктоп", "desktop", "широкий экран", "большой экран", "large screen", "wide screen")


def _viewport_target(text: str) -> str | None:
    low = (text or "").lower()
    narrow = any(c in low for c in _VP_NARROW)
    wide = any(c in low for c in _VP_WIDE)
    if narrow and not wide:
        return "narrow"
    if wide and not narrow:
        return "wide"
    return None   # both device widths named, or neither → any real measurement confirms
# A rendered-surface context that promotes an INTERACTION criterion to dom_contains even
# without a _DOM_CTX word ("browser interaction: … показывает X"). NOT added to _DOM_CTX
# itself — "browser" appears in the page_open criterion too, which must stay page_open;
# the interaction gate only fires when _is_interaction is already true (fill/click/…).
_BROWSER_CTX = ("browser", "браузер", "страниц", "rendered", "dom", "на экране",
                "ui ", "форм", "интерфейс", "отрисов", "верстк", "лендинг")
# Absence: STRONG cues mean the path is gone; WEAK cues ("cleanup") are usually
# TEMPORAL ("существует ДО cleanup") — they only imply absence when there is no
# explicit exist verb, so "директория существует ДО cleanup" is file_exists, not a
# cleanup criterion confirmed with a "не найден" evidence.
_ABSENT_STRONG = ("удал", "removed", "deleted", "не существует", "not exist",
                  "больше нет", "gone", "стёрт", "стерт", "снесён", "снесен")
_ABSENT_WEAK = ("cleanup", "очищ")
_EXIST_CTX = ("существует", "создан", "создана", "создать", "exists", "присутству", "появил",
              "есть файл", "папк", "директор", "directory", "внутри", "находится в",
              "находится именно", "расположен", "лежит в")
# Scope/safety RULES — not verifiable outcomes. They may sit inside a "Критерии
# готовности" section ("изменения внесены именно в текущий проект, без создания нового
# Vite/React проекта"), but they belong in constraints: a verify-only run (no edits)
# can never "confirm" them, so they'd hang forever as unverified.
_SCOPE_RULE_CUES = (
    "без создания", "без создани", "не создавай", "не создавать", "не создавая",
    "не трогай", "не трогать", "не меняй package", "не менять package",
    "не добавляй нов", "не добавлять нов", "не удаляй существ", "не удалять существ",
    "не переписывай", "не переписыв",
    "только текущий проект", "только в текущем проект", "только в текущий проект",
    "если нужны изменения", "если изменения нужны", "если это реально нужно",
    "изменения внесены именно", "изменения вносятся только", "менять только",
    # non-mutation guarantees: "родительский проект не изменён, кроме создания X" is a
    # scope rule — a verify-only run can't "confirm" it, so it belongs in constraints.
    "не изменён", "не изменена", "не изменены", "не изменять файл", "не менять файл",
    "не затрагива", "не затронут", "родительск", "кроме создания", "кроме папк",
    "unchanged", "not modified", "not changed",
)


def _command_kind(text: str) -> str:
    """The specific command a command_check criterion/verdict is about, or 'any' for a
    generic 'the available checks pass'. So a green typecheck confirms a typecheck
    criterion but NOT a separate build criterion."""
    low = (text or "").lower()
    if _has(low, ("доступные провер", "проходят провер", "проверки проход", "все проверки",
                  "сборки/typecheck", "сборки / typecheck", "проект проходит провер", "проходит провер")):
        return "any"
    if _has(low, ("tsc", "typecheck", "type-check", "mypy", "pyright", "ошибок типов", "типизац")):
        return "typecheck"
    if _has(low, ("run build", "vite build", "cargo build", "go build", "webpack", "rollup",
                  "npm build", "сборк", "собира", "билд", "компил", "bundl", "build")):
        return "build"
    if _has(low, ("pytest", "unittest", "npm test", "jest", "go test", "vitest", ".test.", "тест", "test")):
        return "test"
    if "import" in low:
        return "import"
    if "smoke" in low:
        return "smoke"
    if _has(low, ("lint", "линт")):
        return "lint"
    return "any"


def _criterion_intent(text: str) -> str:
    """Domain-agnostic intent from the criterion's TARGET CONTEXT (page/file/server/
    command), not from a product name and not from a bare verb."""
    low = (text or "").lower()
    if _has(low, ("в отчёт", "в отчет", "в финальном отч", "какие команды", "какие файлы",
                  "что создано", "что было создано", "перечисл", "report-only",
                  "описан в отч", "описать в отч", "команды очистки опис")):
        return "report"
    # Context comes from the criterion's PROSE, not from the target strings: a file
    # criterion "содержит строку `dom=verified`" must not read as a DOM criterion just
    # because the pattern contains "dom". So detect the DOM/file context on the text
    # with quoted spans stripped.
    unquoted = _QUOTED_RE.sub(" ", text or "").lower()
    dom = _has(unquoted, _DOM_CTX)
    targets = _dom_targets(text)
    has_path = bool(_path_tokens_from_text(text))
    fil = _has(unquoted, _FILE_CTX) or (has_path and not dom)
    negative = _has(low, _NEG_CTX)
    # Context cues must come from the criterion's PROSE — never from its PATH tokens.
    # A file merely NAMED smoke-canary.txt / server-config.txt / mobile.css must not
    # hijack the intent into command_check / server_started / viewport_layout (live SSH
    # canary: «файл C:\AgentLab\smoke-canary.txt существует» classified command_check
    # off the "smoke" inside its own filename, so a green ssh_exists could never
    # confirm it). Quoted spans are KEPT here — a command criterion's cue legitimately
    # lives inside its quotes («`npm test` проходит»); only path-shaped tokens go.
    prose = low
    for _tok in _path_tokens_from_text(text):
        prose = prose.replace(_tok.lower(), " ")

    if negative:
        # A file NOT containing a pattern is verifiable; a "DOM must NOT show X" is not
        # (no absence-of-render verifier) → generic/unverified, honestly.
        if fil:
            return "content_not_contains"
        # A layout/overflow criterion is INHERENTLY an absence-assertion ("не должно быть
        # горизонтального скролла") — and no_hoverflow IS a direct verifier for it, so route
        # it to viewport_layout instead of the generic fallback (Batch D review, finding 4).
        # fil-first above keeps a "файл … не содержит `mobile`" as content_not_contains.
        if _has(prose, _VIEWPORT_CTX):
            return "viewport_layout"
        return "generic"
    # positive: a rendered-page claim with either a visibility verb OR named tokens
    # (a UI section listing `Inventory`,`Backups`,… is a dom_contains without a verb).
    # An INTERACTION that asserts a result ("browser interaction: … `Validate` показывает
    # `Job name required`") is a DOM claim too, even without a rendered/DOM word — BUT only
    # when it names a browser/page surface, so a file-write ("заполните файл X … содержит
    # Y") or a CLI ("введите команду … вывод содержит Z") isn't hijacked into browser-only.
    if (dom or (_is_interaction(text) and _has(low, _BROWSER_CTX))) and (_has(low, _DOM_VERB) or targets):
        return "dom_contains"
    if _has(prose, _VIEWPORT_CTX):
        return "viewport_layout"
    if fil and _has(low, ("содержит", "contains", "включает", "есть строка")):
        return "content_contains"
    # server BEFORE page_open, but a "browser opens URL" claim is page_open not server
    if _has(prose, _SERVER_CTX) and not (_has(prose, _OPEN_CTX) and _has(low, ("browser", "http", "браузер"))):
        return "server_started"
    if _has(prose, _OPEN_CTX):
        return "page_open"
    if _is_command_output(text):
        return "command_output"        # a command prints text — proven by run_bash stdout
    if _has(prose, _CMD_CTX):
        return "command_check"
    exists_verb = _has(unquoted, _EXIST_CTX)
    absent = _has(unquoted, _ABSENT_STRONG) or (_has(unquoted, _ABSENT_WEAK) and not exists_verb)
    if absent and (fil or has_path):
        return "file_not_exists"
    if exists_verb and (fil or has_path):
        return "file_exists"
    return "generic"


def _is_scope_rule(line: str) -> bool:
    """A scope/safety RULE (change only the current project, don't create a new one),
    even when written inside a criteria section — it's a constraint, not a verifiable
    success criterion."""
    return _has((line or "").lower(), _SCOPE_RULE_CUES)


# A criterion enumerating several required strings — ONLY an explicit "keyword: a, b,
# c" colon-list (never a bare two-quote criterion, which is usually `path` + `pattern`).
_LIST_MARKER_RE = re.compile(
    r"(?:содержит|contains|includ\w*|строки|строчки|lines|значени\w*|записи|поля|following)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_LIST_SPLIT_RE = re.compile(r"\s*[,/;\n]\s*|\s+и\s+|\s+and\s+")


def _split_multi_target(c: str) -> list[str]:
    """Split a FILE-content criterion that enumerates several required strings ("file
    contains lines: A, B, C") into one criterion per string, so each is verified
    independently. Fires ONLY on an explicit colon-list and only for content_contains
    — DOM (all-tokens-visible), single-target, and `path`+`pattern` criteria are
    untouched. Each split keeps the named file. Domain-agnostic — no product words."""
    if _criterion_intent(c) != "content_contains":
        return [c]
    m = _LIST_MARKER_RE.search(c or "")
    if not m:
        return [c]
    items = [x.strip().strip("`«»\"'.") for x in _LIST_SPLIT_RE.split(m.group(1))]
    items = [x for x in items if x and len(x) <= 60]
    if len(items) < 2:
        return [c]
    # keep the file the criterion is about, so each split still targets that file
    file_ref = next((t.strip() for t in _QUOTED_RE.findall(c or "") if _file_tokens(t)), "")
    if not file_ref:
        file_ref = next((t for t in _file_tokens(c) if "/" in t or "\\" in t or "." in t), "")
    base = f"файл `{file_ref}`" if file_ref else "файл"
    return [f"{base} содержит `{it}`" for it in items]


def _run_bash_verdict(cmd: str) -> dict | None:
    """A green run_bash command → a command_check verdict tagged with its kind. A
    bundle grep (findstr/grep) is NOT a verdict — it proves a string is in the bundle,
    not that anything passed or is visible."""
    low = (cmd or "").lower()
    if _has(low, ("findstr", "grep ", "select-string")) and not _has(low, ("pytest", "npm test", "&&")):
        return None
    kind = _command_kind(low)
    if kind == "any":
        # bare run_bash with no recognisable check verb is not a verdict
        if not _has(low, ("tsc", "typecheck", "build", "test", "pytest", "vitest", "jest",
                          "import", "smoke", "lint", "npm run", "cargo", "go ")):
            return None
    return {"intents": {"command_check"}, "command_kind": kind, "files": set()}


def _verifier_verdict(tool_name: str, args: dict, *, evidence: str = "", meta: dict | None = None) -> dict | None:
    """Classify a verifier tool call into generic INTENT(s) + target, or None when it
    is not a verdict. Intent comes from the TOOL; targets from its structured evidence
    — NEVER from a shared path/text alone or the model's words."""
    a = args or {}
    m = meta or {}
    if tool_name == "ssh_assert_contains":
        return {"intents": {"content_contains"}, "files": _file_tokens(str(a.get("path", ""))), "pattern": str(a.get("pattern", "")).lower()}
    if tool_name == "ssh_assert_not_contains":
        return {"intents": {"content_not_contains"}, "files": _file_tokens(str(a.get("path", ""))), "pattern": str(a.get("pattern", "")).lower()}
    if tool_name == "ssh_port_check":
        return {"intents": {"server_started"}, "port": str(a.get("port", "")), "files": set()}
    if tool_name == "ssh_exists":  # present(ok)→file_exists, absent(not ok)→file_not_exists
        return {"intents": {"file_exists", "file_not_exists"},
                "files": _file_tokens(str(a.get("path", ""))), "present_when_ok": True}
    if tool_name == "path_exists":  # LOCAL existence probe — same contract as ssh_exists
        return {"intents": {"file_exists", "file_not_exists"},
                "files": _path_tokens_from_arg(str(a.get("path", ""))), "present_when_ok": True}
    if tool_name == "ssh_not_exists":  # EXPLICIT cleanup assertion: absent (ok) proves
        # file_not_exists, and a still-present path is a real FAIL (asserts="absent").
        return {"intents": {"file_not_exists"}, "files": _file_tokens(str(a.get("path", ""))),
                "present_when_ok": False, "asserts": "absent"}
    if tool_name == "ssh_read":  # a successful read proves the file EXISTS (never absence)
        return {"intents": {"file_exists"},
                "files": _file_tokens(str(a.get("path", ""))), "present_when_ok": True}
    if tool_name == "run_bash":
        cmd = str(a.get("command", ""))
        low = cmd.lower()
        is_grep = _has(low, ("findstr", "grep ", "select-string")) and not _has(low, ("pytest", "npm test", "&&"))
        base = _run_bash_verdict(cmd)                      # command_check (exit-code kind) or None
        intents = set((base or {}).get("intents") or set())
        if not is_grep:                                    # a bundle-grep proves neither check nor output
            intents.add("command_output")
        if not intents:
            return None
        return {"intents": intents, "command_kind": (base or {}).get("command_kind", "any"),
                "files": set(), "command": cmd, "output": (evidence or "").lower(),
                "exit_code": (meta or {}).get("exit_code")}
    if tool_name == "run_server":
        # A dev/app server is up on a real URL/port — structured evidence, not words.
        # A failed start (no server_started/actual_url in meta) is NOT a verdict, so it
        # leaves a server criterion unconfirmed (recoverable), never failed.
        if not (m.get("server_started") or m.get("actual_url") or m.get("verifier")):
            return None
        port = str(m.get("actual_port") or m.get("port") or "")
        return {"intents": {"server_started"}, "port": port, "files": set()}
    if tool_name == "http_api":
        return {"intents": {"page_open"}, "files": set()}
    if tool_name == "browser":
        # A real render → the page loaded (page_open) AND its DOM text is genuine
        # visible-text evidence (dom_contains) — unlike a bundle grep. `interacted` says a
        # real fill/select/check/click actually ran, so an INTERACTION criterion can require
        # the actions to have happened rather than confirm off a plain render.
        return {"intents": {"page_open", "dom_contains", "viewport_layout"},
                "text": (evidence or "").lower(),
                "viewport": m.get("viewport"), "interacted": bool(m.get("interacted")), "files": set()}
    return None


def _verdict_target_matches(item: dict, v: dict) -> bool:
    """INTENT + TARGET match (ignores pass/fail — record() applies ok separately)."""
    intents = v.get("intents") or set()
    it = item["intent"]
    if it not in intents:
        return False
    if it == "server_started":
        cp, vp = item.get("port"), v.get("port")
        return (cp == vp) if cp else True   # named port must match; else any server
    if it == "page_open":
        return True
    if it == "command_check":
        ck, vk = item.get("command_kind") or "any", v.get("command_kind") or "any"
        return ck == "any" or vk == "any" or ck == vk
    if it == "command_output":
        exp = item.get("output_expected", "")
        cmd_c = item.get("command", "")
        if not exp or not cmd_c:
            return False       # command_output needs a NAMED command + a concrete expected token
        ne = _norm_dom(exp)
        if not ne or (" " + ne + " ") not in (" " + _norm_dom(v.get("output", "")) + " "):
            return False       # expected text must be in the run's stdout/stderr (boundary-anchored)
        # the run must actually invoke the named command (interpreter/path/env-agnostic) and
        # not be a text-dumper — so `node ./index.js sample.log` confirms `node index.js
        # sample.log`, but `cat index.js` or a run with a different arg does not.
        return _run_invokes(cmd_c, v.get("command", ""))
    if it == "dom_contains":
        if item.get("interaction") and not v.get("interacted"):
            return False   # an interaction criterion needs the fill/click to have ACTUALLY
            # run — a plain render (or one whose locators all missed) can't confirm it, even
            # if the token is statically present (attribution — Batch B review).
        toks = item.get("targets") or set()
        if not toks:
            return False
        # Punctuation- and whitespace-insensitive (a UI renders a label as a SEPARATE
        # element, often WITHOUT its colon: "Network 192.168.88.0" vs token "Network:
        # 192.168.88.0"). _norm_dom collapses non-`\w.` runs to one space on both sides,
        # keeping adjacency and dots. The match is BOUNDARY-anchored (both sides padded
        # with spaces) so a numeric value can't prefix-match a longer one — else
        # `192.168.88.1` would wrongly confirm against a DOM showing `192.168.88.10`,
        # certifying a WRONG subnet value (Ph6 review).
        text = " " + _norm_dom(v.get("text") or "") + " "
        for t in toks:
            nt = _norm_dom(t)
            if not nt or (" " + nt + " ") not in text:
                return False
        return True
    if it == "viewport_layout":
        # Positive layout evidence: a real viewport measurement must have RUN (checked),
        # not just a render. Attribution — a "mobile" layout claim can't be confirmed by a
        # desktop-width measurement (and vice-versa); on a width-bucket mismatch we return
        # False so it stays unconfirmed (honest), never a false confirm and never a fail.
        vp = v.get("viewport") or {}
        if not vp.get("checked"):
            return False
        want = item.get("viewport_width")
        w = vp.get("width") or 0
        if want == "narrow" and w > 600:
            return False
        if want == "wide" and w < 900:
            return False
        return True
    if it in ("content_contains", "content_not_contains"):
        if item["files"] and v.get("files") and not (item["files"] & v["files"]):
            return False
        pat = v.get("pattern", "")
        return bool(pat) and pat in item["text_low"]
    if it in ("file_exists", "file_not_exists"):
        return bool(item["files"] and v.get("files") and (item["files"] & v["files"]))
    return False


def _verdict_outcome(item: dict, v: dict, ok: bool) -> str | None:
    """'confirm' / 'fail' / None for a criterion given a matching verdict and its ok.

    Existence intents (file_exists / file_not_exists) are CONFIRM-ONLY, keyed on the
    OBSERVED presence — never on a phase we can't see. A post-cleanup absence must not
    FAIL a setup 'file exists' criterion (it existed during setup; it's gone now on
    purpose), and a pre-cleanup presence must not fail a 'file removed' criterion. So
    an existence check only ever confirms the matching state; a mismatch is neutral
    (stays unconfirmed → honest partial), never a hard failure. Every other intent
    takes the tool's ok as the criterion's pass/fail."""
    it = item["intent"]
    if it in ("file_exists", "file_not_exists"):
        if not _verdict_target_matches(item, v):
            return None
        present = ok if v.get("present_when_ok", True) else (not ok)
        if it == "file_exists":
            # A PASSIVE existence probe: presence confirms; absence is neutral (never
            # fail — the file may be legitimately gone post-cleanup).
            return "confirm" if present else None
        # file_not_exists: absence confirms; presence only FAILS for an EXPLICIT
        # cleanup assertion (ssh_not_exists) — a passive ssh_exists that happens to
        # see the file (e.g. a pre-cleanup check) leaves it unconfirmed, not failed.
        if not present:
            return "confirm"
        return "fail" if v.get("asserts") == "absent" else None
    if it == "command_output":
        if not _verdict_target_matches(item, v):
            return None
        ec = v.get("exit_code")
        if item.get("expect_nonzero"):
            return "confirm" if isinstance(ec, int) and ec != 0 else None
        # positive: the run must have SUCCEEDED — a RED run whose output merely contains
        # the token (e.g. a failing test printing "12 passing 3 failing") must NOT confirm.
        return "confirm" if (ec is None or ec == 0) else None
    if it == "command_check":
        # Pass/fail by EXIT CODE when known (a real run always carries it): green→confirm,
        # red→fail. This is what stops a broadened run_bash record from false-confirming a
        # red build via ok=True (the tool ran even though the command exited non-zero). When
        # exit_code is absent (a unit test recording only ok=…) fall back to ok.
        if not _verdict_target_matches(item, v):
            return None
        ec = v.get("exit_code")
        succeeded = (ec == 0) if isinstance(ec, int) else bool(ok)
        return "confirm" if succeeded else "fail"
    if it == "viewport_layout":
        # A measured viewport is a real verdict: no horizontal overflow → confirm; overflow
        # at the tested width → FAIL (broken layout is a genuine red, like a failing test).
        if not _verdict_target_matches(item, v):
            return None
        vp = v.get("viewport") or {}
        return "confirm" if vp.get("no_hoverflow") else "fail"
    if not _verdict_target_matches(item, v):
        return None
    return "confirm" if ok else "fail"


def _criterion_item(text: str) -> dict:
    low = (text or "").lower()
    ports = _SALIENT_RE_NUM.findall(text or "")
    cmd = command_spec(text)
    return {
        "text": text, "text_low": low, "status": "unconfirmed", "verifier": None, "evidence": None,
        "intent": _criterion_intent(text), "files": _path_tokens_from_text(text), "port": ports[0] if ports else "",
        "targets": _dom_targets(text), "command_kind": _command_kind(text),
        "interaction": _is_interaction(text), "conditional": _is_conditional(text),
        "command": cmd["command"], "output_expected": cmd["output_expected"],
        "expect_nonzero": cmd["expect_nonzero"], "viewport_width": _viewport_target(text),
    }


@dataclass
class CriteriaTracker:
    """Per-criterion verification state for ONE run. DONE is decided here, from
    verifier verdicts — not from the model's word. A verdict confirms a criterion ONLY
    when their generic INTENT (command_check/server_started/page_open/dom_contains/
    viewport_layout/file_exists/file_not_exists/content_contains/content_not_contains)
    AND target match — a shared path, a bundle grep, or the model's text never
    confirms a semantic/visibility claim."""

    items: list[dict] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: TaskSpec | None) -> "CriteriaTracker":
        crits = spec.success_criteria if spec else []
        return cls(items=[_criterion_item(c) for c in crits])

    def record(self, *, tool_name: str, args: dict, ok: bool, evidence: str, meta: dict | None = None) -> bool:
        """Feed a verifier verdict (classified by tool + args + structured evidence).
        Confirms/refutes a criterion only on matching intent + target; unrelated
        criteria are untouched. `evidence` carries the rendered DOM text for a browser
        verdict; `meta` carries structured fields (actual_port/viewport).

        Returns True if a criterion changed status (unconfirmed→confirmed/failed) —
        the strongest goal-level progress signal there is, which the strategy router
        uses to re-arm the run (a confirmed criterion is real forward motion)."""
        v = _verifier_verdict(tool_name, args, evidence=evidence, meta=meta)
        if v is None:
            return False
        transitioned = False
        for it in self.items:
            outcome = _verdict_outcome(it, v, ok)
            if outcome == "confirm" and it["status"] != "confirmed":
                it.update(status="confirmed", verifier=tool_name, evidence=evidence or None)
                transitioned = True
            elif outcome == "fail" and it["status"] == "unconfirmed" and not it.get("conditional"):
                # A conditional criterion never hard-FAILS — e.g. `npm run typecheck`
                # exiting non-zero because the script is absent must not fail the task;
                # it stays unconfirmed and finalize_conditionals() marks it skipped.
                it.update(status="failed", verifier=tool_name, evidence=evidence or None)
                transitioned = True
        return transitioned

    def completion_status(self) -> str:
        if not self.items:
            return "none"  # no criteria → task-completion axis is n/a
        # A conditional criterion that isn't confirmed is SKIPPED (n/a), never a blocker
        # — it can't fail the whole task ("если в проекте есть typecheck …").
        blocking = [it for it in self.items if it["status"] != "confirmed" and not it.get("conditional")]
        if any(it["status"] == "failed" and not it.get("conditional") for it in self.items):
            return "failed"
        if not blocking:
            return "confirmed"   # every MANDATORY criterion is confirmed
        if any(it["status"] == "confirmed" for it in self.items):
            return "partial"
        return "unverified"

    def finalize_conditionals(self) -> None:
        """At run end, a conditional criterion still unconfirmed is n/a (its precondition
        wasn't met / wasn't exercised) — mark it 'skipped' so the report shows it
        honestly, not as an unanswered failure. Call once when actually finalizing."""
        for it in self.items:
            if it.get("conditional") and it["status"] in ("unconfirmed", "failed"):
                it["status"] = "skipped"

    def report(self) -> list[dict]:
        return [{k: it[k] for k in ("text", "status", "verifier", "evidence")} for it in self.items]
