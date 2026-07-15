"""Dedicated-bot Telegram client for IT changes.

Uses the executor's OWN bot token (`ELIRA_CHANGE_BOT_TOKEN`) — never the main Elira bot's,
so the two pollers don't fight over updates and the main user has no access to this
channel. The poller subscribes to `callback_query` ONLY, strictly re-checks from.id AND
chat.id via the handler, and NEVER logs callback_data (the raw capability). The approval
message carries the approve/reject tokens only inside the inline-keyboard callback_data,
never in visible text.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request

from . import approvals

logger = logging.getLogger(__name__)
_API = "https://api.telegram.org/bot{token}/{method}"


class DeliveryError(RuntimeError):
    """A Telegram send did not confirm ok — the approval was NOT delivered. Raised so the
    caller (request_plan) fails the plan closed via mark_delivery_failed."""


def _bot_token() -> str:
    return str(os.environ.get("ELIRA_CHANGE_BOT_TOKEN", "")).strip()


def _tg(token: str, method: str, payload: dict, timeout: int = 15) -> dict:
    """Telegram API call over STDLIB urllib (no third-party in the executor TCB). Never
    raises — returns {"ok": False, ...} on any transport/HTTP error so callers stay simple."""
    req = urllib.request.Request(
        _API.format(token=token, method=method),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error_code": getattr(exc, "code", 0)}
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def _format_plan(change_run_id: str, target_id: str, snapshot: dict) -> str:
    if snapshot.get("kind") == "netdata_config":
        service = snapshot.get("service") if isinstance(snapshot.get("service"), dict) else {}
        current = snapshot.get("safe_settings") if isinstance(snapshot.get("safe_settings"), dict) else {}
        before = current.get("global.update_every", "built-in default")
        return ("IT config change — approval required\n"
                f"target: {target_id}\n"
                f"config: {snapshot.get('config_id')}\n"
                f"setting: global.update_every: {before} -> 1\n"
                f"service: {service.get('id')} "
                f"{service.get('active_state')}/{service.get('sub_state')}\n"
                "rollback: automatic on definite post-check failure\n"
                f"change: {change_run_id}")
    return ("IT change — approval required\n"
            f"target: {target_id}\n"
            f"unit: {snapshot.get('id')}\n"
            f"state: {snapshot.get('active_state')}/{snapshot.get('sub_state')} "
            f"MainPID={snapshot.get('main_pid')}\n"
            "operation: restart\n"
            f"change: {change_run_id}")


class ChangeApprovalSender:
    """The executor's plan-send. is_configured() gates plan creation (fail-closed)."""

    def is_configured(self) -> bool:
        users, chats = approvals.approver_allowlists()
        return bool(_bot_token()) and bool(users) and bool(chats)

    def send_approval(self, *, change_run_id: str, target_id: str, snapshot: dict,
                      approve_token: str, reject_token: str) -> None:
        token = _bot_token()
        _, chats = approvals.approver_allowlists()
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": approve_token},   # token ONLY here
            {"text": "⛔ Reject", "callback_data": reject_token},
        ]]}
        text = _format_plan(change_run_id, target_id, snapshot)       # no token in text
        for chat_id in chats:
            resp = _tg(token, "sendMessage",
                       {"chat_id": chat_id, "text": text, "reply_markup": json.dumps(keyboard)})
            if resp.get("ok") is not True:      # 429 / transport failure — NOT delivered
                raise DeliveryError(f"sendMessage not ok: {resp.get('error_code') or resp.get('error')}")


def _dispatch(update: dict, token: str, *, registry_path: str | None, apply_runner,
              user_ids: set[int], chat_ids: set[int]) -> dict:
    """Route ONE callback_query update; answer it. handle_callback enforces the allowlist
    and never logs callback_data."""
    result = approvals.handle_callback(update, registry_path=registry_path,
                                       apply_runner=apply_runner, user_ids=user_ids, chat_ids=chat_ids)
    cq_id = (update.get("callback_query") or {}).get("id")
    if cq_id:
        try:
            _tg(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": result.get("text", "")})
        except Exception:  # noqa: BLE001
            logger.warning("change bot: answerCallbackQuery failed")     # ids only, never data
    return result


def poll_loop(*, registry_path: str | None = None, apply_runner=None, stop=lambda: False) -> None:
    """getUpdates loop subscribed to callback_query ONLY. Runs as the executor principal."""
    token = _bot_token()
    if not token:
        logger.error("change bot: ELIRA_CHANGE_BOT_TOKEN not set — not polling")
        return
    user_ids, chat_ids = approvals.approver_allowlists()
    if not user_ids or not chat_ids:
        logger.error("change bot: approver allowlist empty/malformed — not polling")
        return
    offset = 0
    logger.info("change bot: polling started (callback_query only)")
    while not stop():
        try:
            resp = _tg(token, "getUpdates",
                       {"offset": offset, "timeout": 30, "allowed_updates": ["callback_query"]},
                       timeout=35)
            if not resp.get("ok"):
                time.sleep(5)
                continue
            for update in resp.get("result", []):
                offset = int(update["update_id"]) + 1
                if "callback_query" not in update:      # defensive: only callback_query
                    continue
                _dispatch(update, token, registry_path=registry_path, apply_runner=apply_runner,
                          user_ids=user_ids, chat_ids=chat_ids)
        except Exception as exc:  # noqa: BLE001 — _tg never raises; this covers dispatch bugs
            logger.error("change bot: poll error: %s", exc)
            time.sleep(5)
