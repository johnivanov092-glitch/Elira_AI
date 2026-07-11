"""Dedicated-bot Telegram callback handling for IT changes.

The executor polls its OWN Telegram bot for `callback_query` updates and routes them here.
A decision is honoured ONLY when BOTH `callback_query.from.id` AND
`callback_query.message.chat.id` are in the immutable `ITOPS_CHANGE_APPROVER_*` env
allowlists — never on callback_data alone. The raw capability token in callback_data is
hashed and NEVER logged. All authority is server-side: an approve consumes the one-time
capability and CASes the run into `applying` in one transaction, then the engine applies.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable

from . import engine, transport
from . import store as cs

logger = logging.getLogger(__name__)


def _id_set(env_name: str) -> set[int]:
    """Parse a comma-separated int allowlist. A MALFORMED entry (any non-integer token)
    makes the whole allowlist empty → deny all (fail-closed), never a partial allow."""
    raw = str(os.environ.get(env_name, "")).strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            return set()        # malformed → deny all
    return out


def approver_allowlists() -> tuple[set[int], set[int]]:
    """Immutable approver allowlists from env — user ids AND chat ids. Empty ⇒ deny all
    (fail-closed)."""
    return _id_set("ITOPS_CHANGE_APPROVER_USER_IDS"), _id_set("ITOPS_CHANGE_APPROVER_CHAT_IDS")


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def handle_callback(update: dict[str, Any], *, registry_path: str | None = None,
                    apply_runner: Callable | None = None,
                    user_ids: set[int] | None = None, chat_ids: set[int] | None = None) -> dict[str, Any]:
    """Process one Telegram callback_query. Returns a small result for answerCallbackQuery.
    Never changes state unless BOTH ids are allowlisted AND a valid one-time capability is
    consumed. The raw token is never logged."""
    cq = (update or {}).get("callback_query")
    if not isinstance(cq, dict):
        return {"ok": False, "text": "ignored"}
    allow_users = approver_allowlists()[0] if user_ids is None else user_ids
    allow_chats = approver_allowlists()[1] if chat_ids is None else chat_ids
    from_id = (cq.get("from") or {}).get("id")
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    if not allow_users or not allow_chats or from_id not in allow_users or chat_id not in allow_chats:
        logger.warning("itops change: callback from non-approver (from=%s chat=%s) — denied",
                       from_id, chat_id)                    # ids only, NEVER the token
        return {"ok": False, "text": "not authorized"}
    token = str(cq.get("data") or "")                       # raw capability — never logged
    if not token:
        return {"ok": False, "text": "no action"}
    claimed = cs.consume_capability(capability_hash=_sha(token), approver=f"tg:{from_id}",
                                    apply_deadline_seconds=engine.APPLY_DEADLINE_SECONDS)
    if claimed is None:
        return {"ok": False, "text": "expired or already handled"}
    cid = claimed["change_run_id"]
    if claimed["change_run_status"] == "rejected":
        return {"ok": True, "text": "change rejected", "change_run_id": cid, "status": "rejected"}
    # approve consumed → applying → apply now (server-side, deterministic)
    status = engine.apply(cid, registry_path=registry_path, runner=apply_runner or transport.run)
    return {"ok": status == "applied", "text": f"change {status}", "change_run_id": cid, "status": status}
