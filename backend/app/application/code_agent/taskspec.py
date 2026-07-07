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


def _quoted_tokens(text: str) -> set[str]:
    return {m.strip().lower() for m in _QUOTED_RE.findall(text or "") if m.strip()}


def _dom_targets(text: str) -> set[str]:
    """The literal strings a DOM criterion says must be on the page. Quoted/back-ticked
    tokens first (`VaultDesk`, `Start local audit`); else the phrase after a
    'текст…/text…' marker. Domain-agnostic — any task's tokens, never hardcoded."""
    toks = _quoted_tokens(text)
    if toks:
        return toks
    low = (text or "")
    ll = low.lower()
    for marker in _AFTER_TEXT_MARKERS:
        i = ll.rfind(marker)
        if i >= 0:
            rest = low[i + len(marker):].strip().strip("`«»\"'.,;:()").lower()
            if rest and len(rest) <= 40:
                return {rest}
    return set()


def _has(low: str, cues: tuple[str, ...]) -> bool:
    return any(c in low for c in cues)


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
# Absence: STRONG cues mean the path is gone; WEAK cues ("cleanup") are usually
# TEMPORAL ("существует ДО cleanup") — they only imply absence when there is no
# explicit exist verb, so "директория существует ДО cleanup" is file_exists, not a
# cleanup criterion confirmed with a "не найден" evidence.
_ABSENT_STRONG = ("удал", "removed", "deleted", "не существует", "not exist",
                  "больше нет", "gone", "стёрт", "стерт", "снесён", "снесен")
_ABSENT_WEAK = ("cleanup", "очищ")
_EXIST_CTX = ("существует", "создан", "создана", "создать", "exists", "присутству", "появил",
              "есть файл", "папк", "директор", "directory")
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
    has_path = bool(_file_tokens(text))
    fil = _has(unquoted, _FILE_CTX) or (has_path and not dom)
    negative = _has(low, _NEG_CTX)

    if negative:
        # A file NOT containing a pattern is verifiable; a "DOM must NOT show X" is not
        # (no absence-of-render verifier) → generic/unverified, honestly.
        if fil:
            return "content_not_contains"
        return "generic"
    # positive: a rendered-page claim with either a visibility verb OR named tokens
    # (a UI section listing `Inventory`,`Backups`,… is a dom_contains without a verb).
    if dom and (_has(low, _DOM_VERB) or targets):
        return "dom_contains"
    if _has(low, _VIEWPORT_CTX):
        return "viewport_layout"
    if fil and _has(low, ("содержит", "contains", "включает", "есть строка")):
        return "content_contains"
    # server BEFORE page_open, but a "browser opens URL" claim is page_open not server
    if _has(low, _SERVER_CTX) and not (_has(low, _OPEN_CTX) and _has(low, ("browser", "http", "браузер"))):
        return "server_started"
    if _has(low, _OPEN_CTX):
        return "page_open"
    if _has(low, _CMD_CTX):
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
    if tool_name == "ssh_not_exists":  # EXPLICIT cleanup assertion: absent (ok) proves
        # file_not_exists, and a still-present path is a real FAIL (asserts="absent").
        return {"intents": {"file_not_exists"}, "files": _file_tokens(str(a.get("path", ""))),
                "present_when_ok": False, "asserts": "absent"}
    if tool_name == "ssh_read":  # a successful read proves the file EXISTS (never absence)
        return {"intents": {"file_exists"},
                "files": _file_tokens(str(a.get("path", ""))), "present_when_ok": True}
    if tool_name == "run_bash":
        return _run_bash_verdict(str(a.get("command", "")))
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
        # visible-text evidence (dom_contains) — unlike a bundle grep.
        return {"intents": {"page_open", "dom_contains"}, "text": (evidence or "").lower(),
                "viewport": bool(m.get("viewport")), "files": set()}
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
    if it == "dom_contains":
        toks = item.get("targets") or set()
        text = v.get("text") or ""
        return bool(toks) and all(t in text for t in toks)
    if it == "viewport_layout":
        return bool(v.get("viewport"))
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
    if not _verdict_target_matches(item, v):
        return None
    return "confirm" if ok else "fail"


def _criterion_item(text: str) -> dict:
    low = (text or "").lower()
    ports = _SALIENT_RE_NUM.findall(text or "")
    return {
        "text": text, "text_low": low, "status": "unconfirmed", "verifier": None, "evidence": None,
        "intent": _criterion_intent(text), "files": _file_tokens(text), "port": ports[0] if ports else "",
        "targets": _dom_targets(text), "command_kind": _command_kind(text),
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
            elif outcome == "fail" and it["status"] == "unconfirmed":
                it.update(status="failed", verifier=tool_name, evidence=evidence or None)
                transitioned = True
        return transitioned

    def completion_status(self) -> str:
        if not self.items:
            return "none"  # no criteria → task-completion axis is n/a
        st = [it["status"] for it in self.items]
        if "failed" in st:
            return "failed"
        if all(s == "confirmed" for s in st):
            return "confirmed"
        if any(s == "confirmed" for s in st):
            return "partial"
        return "unverified"

    def report(self) -> list[dict]:
        return [{k: it[k] for k in ("text", "status", "verifier", "evidence")} for it in self.items]
