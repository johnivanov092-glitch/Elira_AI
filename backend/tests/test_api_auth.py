"""Auth gate: loopback is trusted, non-local callers need a valid token."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import auth


TOKEN = "test-secret-token"


# ── Pure decision function ──────────────────────────────────────────────────

def test_loopback_ipv4_is_trusted_without_token():
    assert auth.is_authorized("127.0.0.1", None, token=TOKEN) is True


def test_loopback_ipv6_is_trusted_without_token():
    assert auth.is_authorized("::1", None, token=TOKEN) is True


def test_testclient_host_is_trusted():
    # Starlette in-process client — not network-routable, safe to trust.
    assert auth.is_authorized("testclient", None, token=TOKEN) is True


def test_missing_client_host_treated_as_local():
    assert auth.is_authorized(None, None, token=TOKEN) is True


def test_remote_without_token_is_rejected():
    assert auth.is_authorized("203.0.113.7", None, token=TOKEN) is False


def test_remote_with_wrong_token_is_rejected():
    assert auth.is_authorized("203.0.113.7", "Bearer nope", token=TOKEN) is False


def test_remote_with_correct_bearer_token_is_allowed():
    assert auth.is_authorized("203.0.113.7", f"Bearer {TOKEN}", token=TOKEN) is True


def test_remote_with_raw_token_is_allowed():
    # Accept a bare token (no "Bearer " prefix) too.
    assert auth.is_authorized("203.0.113.7", TOKEN, token=TOKEN) is True


def test_remote_with_empty_expected_token_is_rejected():
    assert auth.is_authorized("203.0.113.7", "Bearer x", token="") is False


def test_disabled_enforcement_allows_everyone():
    assert auth.is_authorized("203.0.113.7", None, token=TOKEN, enabled=False) is True


def test_extract_bearer_variants():
    assert auth.extract_bearer("Bearer abc") == "abc"
    assert auth.extract_bearer("bearer abc") == "abc"
    assert auth.extract_bearer("abc") == "abc"
    assert auth.extract_bearer(None) == ""
    assert auth.extract_bearer("") == ""


def test_is_trusted_host_rejects_lan_addresses():
    assert auth.is_trusted_host("192.168.1.50") is False
    assert auth.is_trusted_host("10.0.0.4") is False
    assert auth.is_trusted_host("not-an-ip") is False


# ── Middleware wiring (via in-process TestClient = trusted host) ─────────────

def test_health_is_open():
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_in_process_client_reaches_protected_route():
    # TestClient host is "testclient" → trusted → no 401 from the auth gate.
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/agent-os/runs")
        assert resp.status_code != 401
