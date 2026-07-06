"""Intent-injected niche rules for the code-agent system prompt.

The base system prompt sits at the num_ctx=8192 compaction boundary — every
always-on token risks the loop/compaction canaries (proven: a ~50-word SSH rule
added to the base prompt broke 8 canaries incl. test_context_compaction).

Niche, rarely-needed rules therefore live HERE and are injected into a run's
system prompt ONLY when the user's task matches. Normal runs pay ZERO tokens, so
the canaries (whose tasks never match) stay green.

Add a rule = append to NICHE_RULES. Keep matchers specific enough that unrelated
runs — and the canary tests — never trigger them. Dependency-free (no imports
from the code_agent package) so prompts.py can import it without a cycle.
"""
from __future__ import annotations

# When the user asks to set up / find / remind SSH access. The whole point of the
# intent-injection is that this (otherwise base-prompt-breaking) block only loads
# on SSH-shaped runs.
_SSH_RULE = (
    "НАСТРОЙКА SSH-ДОСТУПА. Провайдер `ssh_run` работает только для хостов из "
    "allowlist (`data/ssh_acl.json`, Settings→SSH); матч ТОЧНЫЙ по токену, которым "
    "ты зовёшь `ssh_run(host=...)` — алиасы `~/.ssh/config` в самом ACL НЕ "
    "резолвятся, конвенция — читаемые алиасы. Когда просят создать/найти/напомнить "
    "SSH-доступ, отдай пользователю СВЯЗНУЮ пару: (1) блок `Host <алиас>` для "
    "`~/.ssh/config` (HostName/User/IdentityFile) и (2) тот же `<алиас>` для "
    "`allowed_hosts` — оба совпадают с тем, чем будешь звать `ssh_run`. Публичный "
    "ключ (`.pub`) — пользователю для установки на таргет; приватный ключ в чат "
    "НИКОГДА (в конфиге он по пути). Инвентарь бери из `~/.ssh` (ключи, `config`, "
    "`known_hosts`) + `ssh_acl.json`. PowerShell ЧЕРЕЗ ssh шли как "
    "`powershell -EncodedCommand <base64>` — иначе cmd.exe клиента рвёт `|`/кавычки "
    "до отправки."
)

# (id, keyword-matchers, rule-text). Match = case-insensitive substring of any
# keyword in the task text.
NICHE_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "ssh_setup",
        (
            "ssh", "authorized_keys", "known_hosts", "sshpass",
            "ssh-keygen", "id_ed25519", "ssh_acl", "putty",
        ),
        _SSH_RULE,
    ),
)


def select_niche_rules(task_text: str | None) -> list[str]:
    """Rule texts whose matchers fire for this task. Empty for the common case
    (nothing matches) — so a normal run adds zero prompt tokens."""
    if not task_text:
        return []
    low = task_text.lower()
    out: list[str] = []
    for _rule_id, keywords, rule in NICHE_RULES:
        if any(kw in low for kw in keywords):
            out.append(rule)
    return out
