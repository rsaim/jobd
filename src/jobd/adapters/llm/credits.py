"""OpenRouter credit awareness — stop cleanly, never 402 mid-run.

The first full audit of this record died halfway through on
``402: This request requires more credits`` — half a run's spend bought
nothing, and the failure surfaced as five cryptic per-company errors
rather than one sentence about money. The account balance is one GET
away, so every long pipeline now checks it: once before starting (fail
before burning anything) and again at natural boundaries (batches,
phases, waves), stopping gracefully when the floor is reached.

The floor is ``JOBD_MIN_CREDITS`` (dollars, default 1.00) — enough
headroom that in-flight concurrent calls still complete under it.
Checks are cached for 60 seconds so pipelines can call ``guard.ok()``
freely without turning progress into an API hammer; a network failure
reads as "unknown", which never blocks work (a flaky credits endpoint
must not stop a paid-up account).
"""

from __future__ import annotations

import os
import time


class CreditsLow(RuntimeError):
    """Raised by ``CreditGuard.require()`` when the balance is under the
    floor. Message is user-facing — it names the number and the fix."""


class CreditGuard:
    """Cached balance checks against the JOBD_MIN_CREDITS floor."""

    CACHE_SECONDS = 60.0

    def __init__(self, floor: float | None = None) -> None:
        if floor is None:
            try:
                floor = float(os.environ.get("JOBD_MIN_CREDITS", "1.0"))
            except ValueError:
                floor = 1.0
        self.floor = floor
        self._balance: float | None = None
        self._checked_at = 0.0

    def balance(self) -> float | None:
        """Dollars left, or None when unknown (no key, network failure)."""
        now = time.monotonic()
        if now - self._checked_at < self.CACHE_SECONDS:
            return self._balance
        self._checked_at = now
        self._balance = _fetch_balance()
        return self._balance

    def ok(self) -> bool:
        """True while spending may continue. Unknown balances are ok —
        a flaky credits endpoint must not stop a paid-up account."""
        balance = self.balance()
        return balance is None or balance >= self.floor

    def require(self) -> None:
        """Raise ``CreditsLow`` when under the floor — the preflight form."""
        balance = self.balance()
        if balance is not None and balance < self.floor:
            raise CreditsLow(
                f"OpenRouter balance ${balance:.2f} is under the "
                f"${self.floor:.2f} floor (JOBD_MIN_CREDITS) — add credits "
                "at https://openrouter.ai/settings/credits before running "
                "a paid pass."
            )


def _fetch_balance() -> float | None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return None
    try:
        import httpx

        response = httpx.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        data = response.json()["data"]
        return float(data["total_credits"]) - float(data["total_usage"])
    except Exception:
        return None
