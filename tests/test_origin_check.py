"""The cross-site write guard must compare origins, not search strings.

`jobd serve` has no session and no auth — it binds to localhost for one
operator — so the origin check is the only thing standing between a page the
operator happens to have open and every write endpoint on the dashboard
(teach a sender rule, approve an outbound send). It was a substring test,
`host not in origin`, which passes for any attacker host that merely
*contains* the operator's host somewhere: `localhost:8100.evil.com` matches,
and so does `attacker.com/?x=localhost:8100`.
"""

from __future__ import annotations

import pytest

from jobd.web.app import _origin_allowed


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8100",
        "https://localhost:8100",
        "http://localhost:8100/",
        "http://localhost:8100/some/path",
    ],
)
def test_same_origin_is_allowed(origin: str) -> None:
    assert _origin_allowed(origin, "localhost:8100")


@pytest.mark.parametrize(
    "origin",
    [
        # The substring bypasses the old check let through.
        "https://localhost:8100.evil.com",
        "http://evil-localhost:8100.attacker.net",
        "http://attacker.com/?x=localhost:8100",
        "http://attacker.com/#localhost:8100",
        # Ordinary cross-origin.
        "http://evil.com",
        "http://localhost:9999",
        "http://127.0.0.1:8100",
    ],
)
def test_cross_origin_is_refused(origin: str) -> None:
    assert not _origin_allowed(origin, "localhost:8100")


def test_missing_origin_is_refused() -> None:
    assert not _origin_allowed("", "localhost:8100")
    assert not _origin_allowed(None, "localhost:8100")


def test_missing_host_is_refused() -> None:
    """No Host header means nothing to compare against — fail closed."""
    assert not _origin_allowed("http://localhost:8100", "")


def test_garbage_origin_is_refused() -> None:
    assert not _origin_allowed("not a url", "localhost:8100")
    assert not _origin_allowed("://", "localhost:8100")


def test_host_comparison_is_case_insensitive() -> None:
    assert _origin_allowed("http://LocalHost:8100", "localhost:8100")
