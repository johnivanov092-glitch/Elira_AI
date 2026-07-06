"""Progress-aware controller — the deterministic layer around the LLM.

The LLM proposes the next step; THIS module decides whether the world actually
moved, whether the current *method* is worth repeating, and when to stop honestly.
It is the "strategy router", not a guard that blocks work:

    goal → attempt → measurable progress?
                       ├─ yes → continue
                       ├─ no  → this strategy_key is exhausted → switch FAMILY
                       └─ no again (families exhausted / budget blown) → honest stop

Key idea over a flat "N useless calls → stop" streak: attempts are counted per
**strategy_key = (family, target)**. Hammering the same method with tweaked args
(the raw-ssh escaping spiral: 55 distinct commands, one file, zero change) burns
its 2-attempt budget fast and the model is told to pick a *different family* —
while genuinely switching approach resets the count. Repetition guards
(exact/near-dup) live elsewhere; this catches different-looking-but-going-nowhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# "Doing" tools — their job is to CHANGE the world. If one of these fires and
# nothing moved, that's a no-progress attempt against its strategy. Read/search
# tools are exploration (bounded by the near-dup guard), not throttled here.
ACTION_TOOLS: frozenset[str] = frozenset(
    {"run_bash", "run_server", "ssh_run", "ssh_run_ps", "ssh_write", "ssh_replace"}
)
# Reading/searching/verifier tools whose FRESH output is genuine new knowledge →
# progress (so "go read the real file / run the verifier" is rewarded, not punished).
INVESTIGATION_TOOLS: frozenset[str] = frozenset(
    {"read_file", "glob", "grep", "project_map", "recall",
     "web_search", "web_fetch", "http_api", "ssh_read",
     "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check"}
)

# Budgets (see docs/AGENT_RUNTIME_PLAN.md Phase 4). A strategy_key that produces
# no progress this many times is EXHAUSTED → the model must switch family. Once
# this many distinct families are exhausted for one target, the run stops honestly.
# The global cap is a backstop for when classification never marks a family
# exhausted (e.g. every call lands in a slightly different family).
STRATEGY_ATTEMPT_LIMIT = 2
FAMILIES_PER_TARGET_LIMIT = 2
GLOBAL_NO_PROGRESS_CAP = 10
# A single remote host that eats N no-progress "doing" calls (even across
# different command families) is a stuck host — stop rather than keep poking it.
TOOL_HOST_ATTEMPT_LIMIT = 5
# Soft turn-volume nudge: past this many tool calls, remind the model to converge
# (a real cap, but a nudge — productive runs keep going; the no-progress budgets
# above do the actual stopping). Applied in the loop against tool_round_trips.
TURN_TOOL_CALL_SOFT_NUDGE = 25


def _fact_shape(fact: str) -> str:
    """Digit-normalised shape of a grounded fact, for "is this NEW knowledge or a
    re-run of the same check?". A churning netstat/curl/findstr whose only diff is
    a changing PID/port collapses to ONE shape, so it stops reading as progress."""
    low = " ".join((fact or "").split()).lower()
    return re.sub(r"\d+", "N", low)[:160]


def step_made_progress(
    *,
    name: str,
    tool_meta: dict,
    fact: str | None,
    seen_fact_shapes: set[str],
) -> bool:
    """True when this tool call advanced the run toward the goal.

    A "doing" tool counts as progress ONLY when it changes a file — re-running a
    check that returns different bytes but the same state is deliberately NOT
    progress (semantic ok). Mutations (touched_path) and a server starting are
    progress; fresh knowledge from a read/search resets the streak. Mutates
    `seen_fact_shapes`."""
    if tool_meta.get("touched_path"):
        return True  # a file was created / edited / written (local or remote)
    if name == "run_server" and tool_meta.get("ok", True):
        return True  # running state changed
    if name in INVESTIGATION_TOOLS and fact:
        shape = _fact_shape(fact)
        if shape not in seen_fact_shapes:
            seen_fact_shapes.add(shape)
            return True
    return False


# ── strategy classification ─────────────────────────────────────

_EDIT_MARKERS = (
    "set-content", "add-content", "out-file", "writealllines", "writealltext",
    "tee ", "sed -i", ">>", ">", "| set-", "new-item",
)
_CHECK_MARKERS = (
    "findstr", "grep", "select-string", "netstat", "curl", "wget", "test-",
    "get-content", "type ", "cat ", "ls ", "dir ", "tasklist", "ps ", "stat ",
    "test ", "assert", "port", "ping",
)


def _shell_family(command: str, *, remote: bool) -> str:
    c = (command or "").lower()
    prefix = "remote" if remote else "local"
    if any(m in c for m in _EDIT_MARKERS):
        return f"{prefix}_edit_shell"
    if any(c.strip().startswith(m) or m in c for m in _CHECK_MARKERS):
        return "shell_check"
    return f"{prefix}_shell"


def _first_str(args: dict, *keys: str) -> str:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def strategy_family(name: str, args: dict) -> str:
    """The METHOD family of an attempt — stable across arg tweaks, so repeating the
    same approach maps to the same family (and burns one budget) while switching
    approach maps to a new family (and resets)."""
    if name in ("write_file", "edit_file"):
        return "local_edit"
    if name == "ssh_write":
        return "remote_edit:ssh_write"
    if name == "ssh_replace":
        return "remote_edit:ssh_replace"
    if name == "ssh_run_ps":
        return "remote_ps"
    if name == "run_server":
        return "service_start"
    if name == "ssh_run":
        return _shell_family(_first_str(args, "command"), remote=True)
    if name == "run_bash":
        cmd = _first_str(args, "command")
        low = cmd.lower().lstrip()
        if low.startswith("ssh ") and not low.startswith(("ssh-", "sshpass")):
            # raw ssh remote-exec through the shell — the quoting-hell family
            return "remote_edit:inline_ps" if any(m in low for m in _EDIT_MARKERS) else "remote_shell"
        return _shell_family(cmd, remote=False)
    return name  # any other tool: its own name is the family


def strategy_target(name: str, args: dict) -> str:
    """What the attempt acts on — file path / host / port. Combined with the family
    it forms the strategy_key, so 'edit file X two ways' and 'edit file Y' don't
    share a budget."""
    if name in ("ssh_run", "ssh_run_ps", "ssh_read", "ssh_write", "ssh_replace",
                "ssh_port_check", "ssh_assert_contains", "ssh_assert_not_contains"):
        # remote work keys on host:path when a path is present, else host
        host = _first_str(args, "host")
        path = _first_str(args, "path")
        return f"{host}:{path}" if path else host
    if name in ("write_file", "edit_file", "read_file"):
        return _first_str(args, "path")
    if name == "run_bash":
        cmd = _first_str(args, "command")
        # pull the last path-ish / host-ish token so retries of "edit agent-lab.ps1"
        # over ssh share a target
        m = re.findall(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+|[\w.-]+@[\w.-]+|[\w.-]+\.\w{1,5}", cmd)
        return m[-1] if m else ""
    return _first_str(args, "url", "path", "host")


def _suggest_alternatives(family: str) -> str:
    if family.startswith("remote_edit") or family == "remote_ps" or family == "remote_shell":
        return ("ssh_replace(host,path,old,new) для точечной замены, "
                "ssh_write(host,path,content) чтобы записать файл целиком, "
                "или ssh_run_ps(host,script) для PowerShell без экранирования")
    if family in ("local_edit", "local_edit_shell"):
        return "прочитай файл (read_file) и сделай точечный edit_file, или проверь путь"
    if family in ("shell_check", "local_shell"):
        return "смени инструмент: прочитай файл напрямую или используй специализированный примитив"
    return "другой инструмент/подход или спроси пользователя (ask_user)"


@dataclass
class ProgressVerdict:
    status: str                       # "progress" | "no_progress"
    strategy_key: str
    family: str
    target: str
    exhausted: bool                   # this strategy_key just hit its attempt limit
    should_stop: bool                 # runtime should honestly stop now
    redirect: str | None = None       # soft nudge to inject if exhausted (not stop)
    stop_detail: str | None = None    # why we're stopping (for the report)


@dataclass
class ProgressEvaluator:
    """Owns all progress/strategy state for ONE run. `evaluate` is called after
    each tool result and returns what the loop should do."""

    seen_fact_shapes: set[str] = field(default_factory=set)
    strategy_no_progress: dict[str, int] = field(default_factory=dict)
    exhausted: set[str] = field(default_factory=set)
    target_families: dict[str, set[str]] = field(default_factory=dict)
    tool_host_no_progress: dict[str, int] = field(default_factory=dict)
    last_exhausted_family: str | None = None
    progress_events: int = 0
    no_progress_total: int = 0

    def evaluate(self, *, name: str, args: dict, tool_meta: dict, fact: str | None) -> ProgressVerdict:
        family = strategy_family(name, args)
        target = strategy_target(name, args)
        key = f"{family}@{target}"
        host = args.get("host") if isinstance(args.get("host"), str) else ""
        th_key = f"{name}@{host}" if host and name.startswith("ssh") else None

        if step_made_progress(
            name=name, tool_meta=tool_meta, fact=fact, seen_fact_shapes=self.seen_fact_shapes,
        ):
            self.progress_events += 1
            # Movement re-arms this method: clear its no-progress counts and let the
            # family / host be tried again if needed.
            self.strategy_no_progress[key] = 0
            self.exhausted.discard(key)
            fams = self.target_families.get(target)
            if fams:
                fams.discard(family)
            if th_key:
                self.tool_host_no_progress[th_key] = 0
            return ProgressVerdict("progress", key, family, target, exhausted=False, should_stop=False)

        # No progress. Only THROTTLE "doing" tools — a read that returned nothing
        # new is minor churn, handled by the near-dup guard, not the strategy router.
        if name not in ACTION_TOOLS:
            return ProgressVerdict("no_progress", key, family, target, exhausted=False, should_stop=False)

        self.no_progress_total += 1
        self.strategy_no_progress[key] = self.strategy_no_progress.get(key, 0) + 1
        n = self.strategy_no_progress[key]
        just_exhausted = n >= STRATEGY_ATTEMPT_LIMIT and key not in self.exhausted
        if just_exhausted:
            self.exhausted.add(key)
            self.last_exhausted_family = family
            self.target_families.setdefault(target, set()).add(family)
        th_n = 0
        if th_key:
            self.tool_host_no_progress[th_key] = self.tool_host_no_progress.get(th_key, 0) + 1
            th_n = self.tool_host_no_progress[th_key]

        fams_for_target = len(self.target_families.get(target, ()))
        should_stop = (
            fams_for_target >= FAMILIES_PER_TARGET_LIMIT
            or self.no_progress_total >= GLOBAL_NO_PROGRESS_CAP
            or th_n >= TOOL_HOST_ATTEMPT_LIMIT
        )
        redirect = None
        stop_detail = None
        if should_stop:
            if fams_for_target >= FAMILIES_PER_TARGET_LIMIT:
                stop_detail = (
                    f"исчерпаны {fams_for_target} стратегии для «{target or 'цели'}» "
                    f"({', '.join(sorted(self.target_families.get(target, ())))}) — прогресса нет"
                )
            elif th_n >= TOOL_HOST_ATTEMPT_LIMIT:
                stop_detail = f"{th_n} неудачных {name} к «{host}» подряд — хост не двигается"
            else:
                stop_detail = f"{self.no_progress_total} действий подряд без сдвига состояния"
        elif key in self.exhausted:
            redirect = (
                f"Стратегия «{family}» для «{target or 'цели'}» исчерпана "
                f"({n} попытки без прогресса) — НЕ повторяй её. Смени семейство: "
                f"{_suggest_alternatives(family)}."
            )
        return ProgressVerdict(
            "no_progress", key, family, target,
            exhausted=just_exhausted, should_stop=should_stop,
            redirect=redirect, stop_detail=stop_detail,
        )

    def exhausted_summary(self) -> list[str]:
        """Exhausted strategy_keys, for the deterministic final report."""
        return sorted(self.exhausted)

    def next_step_hint(self) -> str:
        """A safe next step for the final report — derived from the last method that
        got stuck, so 'what to try next' is concrete, not boilerplate."""
        if self.last_exhausted_family:
            return _suggest_alternatives(self.last_exhausted_family)
        return "уточни путь или спроси пользователя (ask_user), затем продолжи следующим сообщением"
