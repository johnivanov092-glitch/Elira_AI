"""Elira IT-change privileged executor subsystem.

This package is the *authority* for host-mutating IT operations (v1: a single
`systemctl restart netdata.service`). It is designed to run as a SEPARATE OS
principal (`elira-change-exec`) from the main Elira backend — the account under
which the model's `run_bash` and the code-agent execute.

Security model (see the change-vertical contract): the main process can never
apply a change. It may only, over a narrow rate-limited IPC, `request_plan(target_id)`
and `get_status(change_id)`. The executor owns — inaccessible to the main user —
its own Telegram bot token, target registry, known_hosts, SSH change key, and this
private change store; it verifies the human approval via its dedicated Telegram bot
and is the sole component that resolves a target, builds the SSH argv, and applies.

Nothing here trusts the main it_ops store: a tampered profile/alias there cannot
redirect a change, because host/user/known_hosts/unit/argv come only from the
executor's own service-owned target registry.
"""
