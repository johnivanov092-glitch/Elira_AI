"""Single source of truth for code-agent tool POLICY.

Before this module the four policy sets lived in four different files (base set
in prompts.py, activatable-side-effect set in tools/_meta.py, accept-edits set
and critical set in loop_helpers.py) — so "which tools does the agent have and
under what gate" had no single home, and adding a tool meant remembering to
touch several files. Everything now lives here; the other modules import from it.

Kept dependency-free (no imports from the code_agent package) so prompts.py,
tools/_meta.py and loop_helpers.py can all import it without a cycle.

The gates (recap):
  * BASE_TOOLS               — always visible, no tool_search needed.
  * SEARCH_ACTIVATABLE_SIDE_EFFECT — side-effect tools tool_search may activate
                               even in "ask"/"accept_edits" mode. In "bypass"
                               tool_search activates ANY side-effect tool.
  * EDIT_ONLY_TOOLS          — compatibility inventory for filesystem-shaped work;
                               the shared approval policy owns the real decision.
  * CRITICAL_TOOLS           — explicit high-risk tool identities; call-level shell
                               impact is classified by the shared approval policy.
                               (Destructive SHELL commands are judged per-command
                               by _shell.is_shell_critical, not by tool name.)
Being in a set grants VISIBILITY / auto-APPROVAL only — the executor still
enforces scope, policy and the forbidden tier at dispatch.
"""
from __future__ import annotations

# Always available from the first step (no tool_search round-trip).
BASE_TOOLS: tuple[str, ...] = (
    "read_file", "glob", "grep", "project_map", "recall", "remember",
    "todo_update", "delegate_task",
    "write_file", "edit_file", "run_bash", "run_server",
    "web_search", "web_fetch", "http_api",
)
# NOTE: `path_exists` is deliberately NOT in BASE_TOOLS — the base prompt is at the
# compaction-canary capacity (see memory: any base growth flips the 8192-ctx canary).
# It is a read-only tool, so tool_search activates it on demand; the closure plan and
# route guidance name it, and the base prompt already tells the model to tool_search a
# tool that isn't in the current list.

# Narrowed base for the read-only persona posture (mode "Личный").
READONLY_TOOLS: tuple[str, ...] = ("read_file", "glob", "grep", "recall")

# Side-effect tools tool_search may activate in normal (ask/accept_edits) modes;
# bypass lifts the restriction for ANY side-effect tool. This lists every curated
# native side-effect tool — so the only thing still gated at activation is an
# UNKNOWN/uncurated side-effect tool (e.g. a freshly-added plugin), which stays
# hidden until vetted. Activation is visibility-only; the executor's approval gate
# still asks before any of these actually runs in non-bypass modes.
SEARCH_ACTIVATABLE_SIDE_EFFECT: frozenset[str] = frozenset(
    {
        "computer", "sandbox_run", "sandbox_reset", "sql", "file_gen", "archiver",
        "encrypt", "webhook", "screenshot", "itops_change_apply",
    }
)

# Compatibility inventory for filesystem-shaped, non-shell/non-net work.
EDIT_ONLY_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "file_gen", "converter", "archiver", "sandbox_reset"}
)

# Explicit high-risk tool identities. Reserved for future tools; destructive shell
# is handled per-command by app.change_executor.policy.
CRITICAL_TOOLS: frozenset[str] = frozenset()
