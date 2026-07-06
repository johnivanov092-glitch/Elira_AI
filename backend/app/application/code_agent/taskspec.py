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


# Section headers (case-insensitive, exact match on the pre-colon token).
_HEADERS: dict[str, tuple[str, ...]] = {
    "goal": ("цель", "goal", "задача", "task", "objective"),
    "constraints": ("ограничения", "constraints", "нельзя", "запрещено"),
    "criteria": ("критерии", "критерии готовности", "success criteria", "проверить",
                 "definition of done", "готовность", "acceptance", "checks"),
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
    # Match the exact header OR its LAST word — so a phrased header like
    # "После запуска проверить:" / "Что проверить:" is recognised, not only the
    # bare "Проверить:". Only fires on a `<phrase>:` line, so it stays specific to
    # structured tasks (a plain sentence is never a header).
    low = token.strip().lower().rstrip(":").strip()
    words = low.split()
    last = words[-1] if words else ""
    for section, keys in _HEADERS.items():
        if low in keys or (last and last in keys):
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
    section = "goal"
    saw_goal_header = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Header line ("Цель:", "Ограничения:", "Критерии готовности:") — may carry
        # inline text after the colon.
        if ":" in line:
            head, _, rest = line.partition(":")
            sec = _match_header(head)
            if sec is not None:
                section = sec
                if sec == "goal":
                    saw_goal_header = True
                rest = rest.strip()
                if rest:
                    _route(section, rest, goal_lines, criteria, constraints, stop)
                continue
        bullet = _BULLET_RE.match(raw)
        if bullet:
            _route(section if section != "goal" else "criteria",
                   bullet.group(1).strip(), goal_lines, criteria, constraints, stop)
            continue
        _route(section, line, goal_lines, criteria, constraints, stop)

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

    return TaskSpec(
        goal=goal,
        constraints=_dedupe(constraints),
        success_criteria=_dedupe(criteria),
        verifiers=_dedupe(verifiers),
        stop_conditions=_dedupe(stop),
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


def _route(section: str, line: str, goal, criteria, constraints, stop) -> None:
    if section == "goal":
        goal.append(line)
    elif section == "constraints":
        constraints.append(line)
    elif section == "stop":
        stop.append(line)
    elif section == "report":
        return  # report-only requirement (FIX-9 #3) — not a verifiable criterion
    else:  # criteria
        if _criterion_intent(line) == "report":
            return  # inline "описано в отчёте / что создано" — a report requirement, not a criterion
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
    if spec.success_criteria:
        parts.append("Критерии готовности (докажи verifier'ом, НЕ словами):")
        parts.extend(f"- {c}" for c in spec.success_criteria[:10])
    if spec.verifiers:
        parts.append("Чем проверять: " + "; ".join(spec.verifiers[:8]))
    if spec.constraints:
        parts.append("Ограничения: " + "; ".join(spec.constraints[:8]))
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
    r"[A-Za-z]:\\[^\s'\"<>|]+"        # C:\AgentLabCanary  or  C:\AgentLab\agent-lab.ps1
    r"|(?:/[\w.\-]+){2,}"              # /var/agent/health.txt
    r"|[\w\-]+\.\w{1,5}"              # health.txt, test.ps1
)


def _file_tokens(text: str) -> set[str]:
    """File/path targets in `text` — full path AND basename, so a criterion naming
    `agent-lab.ps1` matches a verifier on `C:\\AgentLab\\agent-lab.ps1`, and a
    directory criterion `C:\\AgentLabCanary` matches an ssh_exists on the same dir."""
    toks: set[str] = set()
    for m in _SALIENT_RE_FILE.findall(text or ""):
        m = m.rstrip(".,;:!?)»\"'").lower()  # drop trailing prose punctuation
        if not m:
            continue
        toks.add(m)
        toks.add(re.split(r"[\\/]", m)[-1])  # basename
    return toks


# Criterion / verifier INTENT — a path alone must NOT confirm a content criterion.
# not_contains is checked before contains ("не содержит" ⊃ "содержит"). Order matters.
_INTENT_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # report intent = "state it in the report", NOT anything with the word "отчёт":
    # a criterion may legitimately verify a report file's content. Cues are the
    # report-REQUIREMENT phrasings only (John's canonical: "cleanup описана в
    # финальном отчёте"), so a real verifiable criterion is never dropped.
    ("report", ("в отчёт", "в отчет", "в финальном отч", "какие команды", "какие файлы",
                "что создано", "что было создано", "перечисл", "report-only",
                "описан в отч", "описать в отч", "команды очистки опис")),
    ("not_contains", ("не содержит", "не должно быть", "не должен содержать", "does not contain",
                      "not contain", "убран", "удал", "отсутствует", "нет строки", "без строки", "removed")),
    ("contains", ("содержит", "contains", "включает", "есть строка", "должен быть", "должна быть",
                  "возвращает", "returns", "отвечает", "responds")),
    ("port", ("порт", "port", "listening", "слушает")),
    ("process", ("процесс", "process", "запущен", "running", "работает", "сервис поднят")),
    ("test", ("pytest", "unittest", "проходит", "зелён", "8/8", "passes", "тест ", "test ", ".ps1 проход")),
    ("exists", ("существует", "создан", "создана", "создать", "exists", "присутствует",
                "папк", "директор", "directory", "файл создан")),
)


def _criterion_intent(text: str) -> str:
    low = (text or "").lower()
    for intent, cues in _INTENT_CUES:
        if any(c in low for c in cues):
            return intent
    return "generic"


def _verifier_verdict(tool_name: str, args: dict) -> dict | None:
    """Classify a verifier tool call into an INTENT + target (path/port/pattern),
    or None when the tool is not a verifier verdict. A path alone never carries
    intent — the tool determines it."""
    a = args or {}
    if tool_name == "ssh_assert_contains":
        return {"intent": "contains", "files": _file_tokens(str(a.get("path", ""))), "pattern": str(a.get("pattern", "")).lower()}
    if tool_name == "ssh_assert_not_contains":
        return {"intent": "not_contains", "files": _file_tokens(str(a.get("path", ""))), "pattern": str(a.get("pattern", "")).lower()}
    if tool_name == "ssh_port_check":
        return {"intent": "port", "port": str(a.get("port", "")), "files": set()}
    if tool_name == "ssh_exists":  # dedicated exists verifier (Test-Path / test -e)
        return {"intent": "exists", "files": _file_tokens(str(a.get("path", "")))}
    if tool_name == "run_bash":
        cmd = str(a.get("command", "")).lower()
        if any(t in cmd for t in ("pytest", "unittest", "npm test", "jest", "go test", "vitest")) or ".test." in cmd:
            return {"intent": "test", "files": _file_tokens(cmd)}
    return None


def _verdict_confirms(item: dict, v: dict) -> bool:
    """A verdict confirms/refutes a criterion ONLY on matching INTENT + salient
    target. Path alone never confirms a content or existence claim."""
    if item["intent"] != v["intent"]:
        return False
    intent = v["intent"]
    if intent == "port":
        return bool(item.get("port")) and item["port"] == v.get("port")
    if intent in ("contains", "not_contains"):
        # same file (if both name one) AND the verifier's pattern appears in the criterion
        if item["files"] and v.get("files") and not (item["files"] & v["files"]):
            return False
        pat = v.get("pattern", "")
        return bool(pat) and pat in item["text_low"]
    if intent in ("exists", "test", "process"):
        return bool(item["files"] and v.get("files") and (item["files"] & v["files"]))
    return False


def _criterion_item(text: str) -> dict:
    low = (text or "").lower()
    ports = _SALIENT_RE_NUM.findall(text or "")
    return {
        "text": text, "text_low": low, "status": "unconfirmed", "verifier": None, "evidence": None,
        "intent": _criterion_intent(text), "files": _file_tokens(text), "port": ports[0] if ports else "",
    }


@dataclass
class CriteriaTracker:
    """Per-criterion verification state for ONE run. DONE is decided here, from
    verifier verdicts — not from the model's word. A verdict confirms a criterion
    ONLY when their INTENT (exists/contains/not_contains/port/test) AND salient
    target match — a shared path alone never confirms a semantic claim."""

    items: list[dict] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: TaskSpec | None) -> "CriteriaTracker":
        crits = spec.success_criteria if spec else []
        return cls(items=[_criterion_item(c) for c in crits])

    def record(self, *, tool_name: str, args: dict, ok: bool, evidence: str) -> None:
        """Feed a verifier verdict (classified by tool + args). Confirms/refutes a
        criterion only on matching intent + target; unrelated criteria are untouched."""
        v = _verifier_verdict(tool_name, args)
        if v is None:
            return
        for it in self.items:
            if not _verdict_confirms(it, v):
                continue
            if ok and it["status"] != "confirmed":
                it.update(status="confirmed", verifier=tool_name, evidence=evidence or None)
            elif not ok and it["status"] == "unconfirmed":
                it.update(status="failed", verifier=tool_name, evidence=evidence or None)

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
