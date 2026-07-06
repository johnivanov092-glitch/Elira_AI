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


def derive_task_spec(task_text: str | None) -> TaskSpec | None:
    """Heuristic TaskSpec, or None when the task isn't structured enough to bother
    (simple/conversational). Never raises; conservative on purpose."""
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

    # Fire only on a genuinely structured task: explicit criteria, OR a stated goal
    # backed by a hard verifier. Otherwise None (no spec, no gate, no tokens).
    if not criteria and not (saw_goal_header and verifiers):
        return None

    return TaskSpec(
        goal=goal,
        constraints=_dedupe(constraints),
        success_criteria=_dedupe(criteria),
        verifiers=_dedupe(verifiers),
        stop_conditions=_dedupe(stop),
    )


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
