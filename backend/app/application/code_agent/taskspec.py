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


def _match_header(token: str) -> str | None:
    low = token.strip().lower().rstrip(":").strip()
    for section, keys in _HEADERS.items():
        if low in keys:
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
    else:  # criteria
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
_SALIENT_RE_QUOTED = re.compile(r"[«\"']([^«»\"']{2,})[»\"']")
_SALIENT_RE_FILE = re.compile(r"[\w.\-/\\]+\.\w{1,5}")
# Hyphenated tech identifiers (Content-Length, Get-Content, agent-lab) — distinctive
# enough to match on, and ordinary prose words are not hyphenated, so it stays safe.
_SALIENT_RE_HYPHEN = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)+")


def _salient_tokens(text: str) -> set[str]:
    """Distinctive tokens for matching a criterion to a verifier result: numbers
    (ports), quoted strings, filenames, hyphenated identifiers. Deliberately NARROW
    — no bare prose words — so a criterion is only ever matched on a real shared
    token. Conservative on purpose: an ambiguous match must not falsely confirm."""
    t = text or ""
    toks: set[str] = set(_SALIENT_RE_NUM.findall(t))
    toks |= {m.strip().lower() for m in _SALIENT_RE_QUOTED.findall(t)}
    toks |= {m.lower() for m in _SALIENT_RE_FILE.findall(t)}
    toks |= {m.lower() for m in _SALIENT_RE_HYPHEN.findall(t)}
    return {x for x in toks if x}


@dataclass
class CriteriaTracker:
    """Per-criterion verification state for ONE run. DONE is decided here, from
    verifier verdicts — not from the model's word. Status per criterion is
    `unconfirmed` (no matching verifier ran), `confirmed` (a matching verifier
    passed) or `failed` (a matching verifier ran red)."""

    items: list[dict] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: TaskSpec | None) -> "CriteriaTracker":
        crits = spec.success_criteria if spec else []
        return cls(items=[
            {"text": c, "status": "unconfirmed", "verifier": None, "evidence": None,
             "tokens": _salient_tokens(c)}
            for c in crits
        ])

    def record(self, *, tool_name: str, ok: bool, evidence: str, arg_text: str) -> None:
        """Feed a verifier verdict. Matches a criterion only on a SHARED salient
        token (conservative); passing → confirmed, red → failed. No shared token →
        nothing touched (the criterion stays unconfirmed)."""
        vtokens = _salient_tokens(arg_text) | _salient_tokens(evidence)
        if not vtokens:
            return
        for it in self.items:
            if not (it["tokens"] & vtokens):
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
