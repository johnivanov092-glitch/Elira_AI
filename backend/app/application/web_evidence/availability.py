"""Demand-driven availability history; diagnostic metadata, never model training.

HTTP and browser reads have separate histories. Path failures do not condemn
an entire site; only connection failures and rate limiting affect the origin.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from app.infrastructure.web_corpus import store

logger = logging.getLogger(__name__)


def _identity(url: str, channel: str) -> tuple[str, str, str] | None:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            return None
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        port = parts.port or (443 if parts.scheme == "https" else 80)
        origin = f"{parts.scheme}://{host}:{port}"
        # No paths, query strings, credentials, prompts or page content go into
        # the availability table. Fragments share the same network resource.
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        return (digest(channel + ":url:" + urlunsplit(parts._replace(fragment=""))),
                digest(channel + ":origin:" + origin), host)
    except (ValueError, UnicodeError):
        return None


_REASON_TEXT = {"access_wall": "стены входа или проверки от ботов"}


def _notice(row: dict) -> str:
    reason = _REASON_TEXT.get(row.get("reason") or "", row.get("reason") or "ошибка чтения")
    if row.get("probing"):
        return "Источник уже проверяется другим чтением; используй другой источник или полученный результат."
    if row.get("stopped"):
        return (f"Источник неоднократно недоступен для автоматического чтения ({reason}); "
                "автоматические попытки приостановлены после проверок через 1, 2 и 4 недели. "
                "Используй альтернативный источник. По прямой просьбе пользователя доступен force_refresh=true.")
    until = datetime.fromtimestamp(float(row.get("retry_at") or 0), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f"Источник временно пропущен после {reason}; следующая проверка при обращении после {until}. "
            "Это история доступности, не утверждение, что сайт закрыт. Используй другой источник.")


def _history_allowed() -> bool:
    from app.application.code_agent.tools._shell import _CURRENT_RUN_ID
    from app.application.code_agent.loop_helpers import run_persistence_policy, web_cache_write_allowed

    run_id = _CURRENT_RUN_ID.get()
    return not run_id or web_cache_write_allowed(run_persistence_policy(str(run_id)))


def begin(url: str, *, channel: str = "http", force: bool = False) -> dict:
    if not _history_allowed():
        return {}
    identity = _identity(url, channel)
    if identity is None:
        return {}
    try:
        probe = store.begin_site_access(*identity, force=force)
        probe["channel"] = channel
        if not probe["allowed"]:
            probe["blocked"] = _notice(probe)
        return probe
    except store.StoreUnavailable as exc:
        logger.warning("Site availability history unavailable: %s", exc)
        return {}  # A diagnostic-store outage does not disable web access.


def _failure(status: int | None, error: str) -> tuple[str, bool]:
    if str(error or "").startswith("access_wall"):
        # HTTP 200 with a login redirect, anti-bot check or JS stub (John 2026-10-06):
        # a failure of this page only — a wall on one path never condemns a site.
        return "access_wall", False
    if status in {401, 403, 404, 408, 410, 429, 500, 502, 503, 504}:
        return f"http_{status}", status == 429
    text = str(error or "").lower()
    if any(marker in text for marker in ("cancelled", "canceled", "ssrf", "invalid url", "fragment")):
        return "", False
    if any(marker in text for marker in ("name or service not known", "name resolution", "getaddrinfo", "err_name_not_resolved")):
        return "dns_failure", True
    if any(marker in text for marker in ("connection refused", "err_connection_refused", "connecttimeout", "connect timeout")):
        return "connection_failure", True
    if "timeout" in text or "timed out" in text:
        return "read_timeout", False
    return "", False  # Parsing, cancellation and unknown outcomes are not site failures.


def finish(probe: dict, *, ok: bool, status: int | None = None, error: str = "", retry_after: str = "",
           final_url: str = "", origin_scope: bool = True) -> None:
    if not probe.get("allowed"):
        return
    reason, origin_failure = _failure(status, error)
    if final_url:
        final = _identity(final_url, probe.get("channel", "http"))
        if final is None or final[1] != probe["origin_key"]:
            origin_scope = False
    origin_failure = origin_failure and origin_scope
    delay = 0.0
    if retry_after:
        try:
            delay = max(0, float(retry_after))
        except ValueError:
            try:
                delay = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
        # Reject non-finite or unreasonable server metadata without throwing.
        if not 0 <= delay <= 365 * 86400:
            delay = 0
    try:
        store.finish_site_access(probe, success=ok or (status is not None and 200 <= status < 300),
                                 reason=reason, origin_failure=origin_failure, retry_after=delay)
    except store.StoreUnavailable as exc:
        logger.warning("Site availability result was not stored: %s", exc)


def notes(urls: list[str], *, channel: str = "http") -> dict[str, str]:
    if not _history_allowed():
        return {}
    identities = {url: identity for url in urls[:100] if (identity := _identity(url, channel))}
    try:
        rows = store.site_access_rows(list(dict.fromkeys(key for identity in identities.values() for key in identity[:2])))
    except store.StoreUnavailable as exc:
        logger.warning("Site availability lookup unavailable: %s", exc)
        return {}
    now = time.time()
    result = {}
    for url, identity in identities.items():
        for key in identity[:2]:
            row = rows.get(key, {})
            if row.get("stopped") or row.get("retry_at", 0) > now:
                result[url] = _notice(row)
                break
    return result
