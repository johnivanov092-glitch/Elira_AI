"""The legacy SSRF seam validates URL shape without blocking destinations."""
from __future__ import annotations

import pytest

from app.application.web.ssrf_guard import check_ssrf


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/api",
        "http://127.0.0.1/admin",
        "https://192.168.1.5:8443/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
    ],
)
def test_any_http_destination_is_allowed(url: str) -> None:
    assert check_ssrf(url) is None


@pytest.mark.parametrize(
    "url",
    ["", "   ", "ftp://example.com/", "file:///etc/passwd", "https:///missing-host"],
)
def test_unusable_url_shape_is_rejected(url: str) -> None:
    assert check_ssrf(url) is not None


def test_legacy_loopback_ports_argument_is_not_an_allowlist() -> None:
    assert check_ssrf("http://localhost:9999", allow_loopback_ports={3000}) is None
