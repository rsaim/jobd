"""OpenRouter credit awareness — stop cleanly, never 402 mid-run.

Two guards, one object:

* **A balance floor** (``JOBD_MIN_CREDITS``, default $1.00) — enough headroom
  that in-flight concurrent calls still complete under it. The account
  balance is one GET away, so every paid run checks it: once before starting
  (fail before burning anything) and again at natural boundaries.
* **A run budget** (``JOBD_RUN_BUDGET``, or ``--budget``) — a hard ceiling on
  what this one run may spend, required before any paid run starts. A run
  without a budget is refused, not run: the whole point is that surprise
  spend requires an explicit decision to allow it.

:class:`CreditGuard` is called by :class:`LiteLLMProvider` around *every* paid
call — ``before_call`` gates the spend (budget left? balance above the
floor?), ``after_call`` records the actual cost and re-checks — so the ceiling
holds per-call, not per-batch. Checks are cached for 60 seconds so the
per-call hook does not turn progress into an API hammer; a network failure
reads as "unknown", which never blocks work (a flaky credits endpoint must not
stop a paid-up account).

``spent`` is dollars actually charged this run, read off each response's own
usage — not estimated up front. That is what makes the budget a real ceiling:
it is enforced against what was actually billed, one call at a time.
"""

from __future__ import annotations

import os
import threading
import time


class GuardAbort(BaseException):
    """Base for the run-aborting credit/budget signals.

    Deliberately ``BaseException``, not ``Exception``: budget exhaustion and a
    drained balance must stop the *entire* run immediately, wherever they
    surface — and the pipeline is full of per-item ``except Exception`` guards
    whose whole job is to keep one bad message from costing the batch. A guard
    signal must pass those boundaries (and the classify retry loop) untouched,
    the way ``KeyboardInterrupt`` does. Subclasses are converted to a
    ``click.ClickException`` at the CLI edge, so the user sees one sentence,
    not a traceback.
    """


class CreditsLow(GuardAbort):
    """Raised when the account balance is under the floor. Message is
    user-facing — it names the number and the fix."""


class BudgetRequired(GuardAbort):
    """Raised when a paid OpenRouter run has no budget set. Message is
    user-facing — it names the flag/env var to set."""


class BudgetExhausted(GuardAbort):
    """Raised when a run has spent its declared budget. Message is
    user-facing — it names the budget and the amount spent."""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float_optional(name: str) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class CreditGuard:
    """Cached balance + budget checks, called per-call by the provider.

    Thread-safe: the provider makes concurrent calls (classify's LLM pool,
    verify's concurrency), and several may reach ``before_call``/``after_call``
    at once. The balance cache refresh is double-checked under a lock and the
    network GET happens *outside* it (a 10s timeout must not serialize the
    pool); ``spent`` is updated under the same lock. The cost of the lock is
    nothing next to the round trip it guards.
    """

    CACHE_SECONDS = 60.0

    def __init__(self, *, floor: float | None = None, budget: float | None = None) -> None:
        if floor is None:
            floor = _env_float("JOBD_MIN_CREDITS", 1.0)
        if budget is None:
            budget = _env_float_optional("JOBD_RUN_BUDGET")
        self.floor = floor
        self.budget = budget
        self.spent = 0.0
        self._balance: float | None = None
        self._checked_at = 0.0
        self._lock = threading.Lock()

    def balance(self) -> float | None:
        """Dollars left, or None when unknown (no key, network failure)."""
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < self.CACHE_SECONDS:
                return self._balance
            self._checked_at = now
        balance = _fetch_balance()
        with self._lock:
            self._balance = balance
        return balance

    @property
    def remaining(self) -> float | None:
        """Budget not yet spent, or None when no budget is set."""
        if self.budget is None:
            return None
        with self._lock:
            return max(self.budget - self.spent, 0.0)

    def require(self) -> None:
        """Preflight for a paid run: a budget must exist, and the balance must
        be above the floor. Raise before any money moves."""
        if self.budget is None:
            raise BudgetRequired(
                "No OpenRouter budget set — pass --budget (dollars) or set "
                "JOBD_RUN_BUDGET before a paid run."
            )
        balance = self.balance()
        if balance is not None and balance < self.floor:
            raise CreditsLow(
                f"OpenRouter balance ${balance:.2f} is under the "
                f"${self.floor:.2f} floor (JOBD_MIN_CREDITS) — add credits "
                "at https://openrouter.ai/settings/credits before running "
                "a paid pass."
            )

    def before_call(self) -> None:
        """Gate one paid call: budget not exhausted, balance above the floor."""
        self.require()
        with self._lock:
            spent = self.spent
        if self.budget is not None and spent >= self.budget:
            raise BudgetExhausted(
                f"OpenRouter budget ${self.budget:.2f} exhausted "
                f"(${spent:.2f} spent) — raise it with --budget/JOBD_RUN_BUDGET "
                "and re-run to resume."
            )

    def after_call(self, cost: float) -> None:
        """Record what one call actually cost, then re-check the ceiling.

        ``cost`` is the billed amount from the response's own usage, so the
        budget is enforced against real spend, not an estimate."""
        with self._lock:
            self.spent += cost
            spent = self.spent
        if self.budget is not None and spent >= self.budget:
            raise BudgetExhausted(
                f"OpenRouter budget ${self.budget:.2f} exhausted "
                f"(${spent:.2f} spent) — raise it with --budget/JOBD_RUN_BUDGET "
                "and re-run to resume."
            )
        balance = self.balance()
        if balance is not None and balance < self.floor:
            raise CreditsLow(
                f"OpenRouter balance ${balance:.2f} dropped under the "
                f"${self.floor:.2f} floor (JOBD_MIN_CREDITS) mid-run — add "
                "credits at https://openrouter.ai/settings/credits and re-run."
            )

    def ok(self) -> bool:
        """True while spending may continue. Unknown balances are ok — a
        flaky credits endpoint must not stop a paid-up account."""
        balance = self.balance()
        return balance is None or balance >= self.floor


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
