"""Resolution eval: the entity-resolution layer, measured offline.

The classifier is measured per-message (`jobd_classify.py`); this measures the
layer that turns per-message predictions into a record — application windowing
(`pick_application`) and duplicate detection (`suggest_merges`). Deterministic
assertions, no model: the resolution rules are pure functions, so their
correctness is a matter of known scenarios, not vibes. This is
algorithm-improvements.md #8 — the riskiest code in the system (resolution) is
the least measured, and this closes that gap without a database.

Run:  .venv/bin/python evals/run.py --resolution
"""

from __future__ import annotations

from datetime import datetime, timedelta

from jobd.domain.record import Company
from jobd.domain.resolve import (
    ApplicationWindow,
    normalise_company,
    pick_application,
    same_role,
)
from jobd.services.dedupe import suggest_merges

_UTC = datetime(2024, 1, 1).tzinfo


def _company(name: str, domain: str | None = None) -> Company:
    return Company(
        canonical_name=name,
        domain=domain,
        first_seen_at=datetime(2024, 1, 1),
        last_seen_at=datetime(2024, 1, 1),
        #: The name doubles as a stable id — `suggest_merges` only checks
        #: id is not None, so this keeps the offline scenarios faithful to
        #: production's "every company has a row id" assumption.
        id=name,  # type: ignore[arg-type]
    )


def _failures() -> list[str]:
    failures: list[str] = []

    # -- name normalisation -------------------------------------------------
    if normalise_company("Acme, Inc.") != "acme":
        failures.append(f"normalise_company should strip corporate suffixes: {normalise_company('Acme, Inc.')!r}")
    if normalise_company("Acme Robotics") != "acme robotics":
        failures.append("normalise_company should lower and keep words")

    # -- role matching ------------------------------------------------------
    if not same_role("Senior Backend Engineer", "Backend Engineer"):
        failures.append("seniority words must be ignored by same_role")
    if same_role("Backend Engineer", "Product Manager"):
        failures.append("unrelated roles must not match")

    # -- application windowing ----------------------------------------------
    # The 2021-and-2024 case: same role, but outside the window -> a new
    # application, never a fusion (M3 gate 3).
    old = ApplicationWindow(
        id="old", role_title="Backend Engineer",
        started_at=datetime(2021, 3, 1),
        ended_at=datetime(2021, 6, 1),
    )
    picked = pick_application(
        [old], occurred_at=datetime(2024, 5, 1), role_title="Backend Engineer"
    )
    if picked is not None:
        failures.append("a role outside the window must not match (2021 vs 2024)")
    picked = pick_application(
        [old], occurred_at=datetime(2021, 4, 1), role_title="Backend Engineer"
    )
    if picked is None or picked.id != "old":
        failures.append("a role inside the window must match")

    # -- duplicate detection ------------------------------------------------
    companies = [
        _company("Acme Robotics"),
        _company("Acme Robotics", domain="acmerobotics.com"),
        _company("Runway"),
        _company("Runwayml", domain="runwayml.com"),
        _company("Anthropic", domain="anthropic.com"),
        _company("OpenAI", domain="openai.com"),
    ]
    pairs = suggest_merges(companies)
    def joined(left: str, right: str) -> bool:
        return any(
            (c.left_name == left and c.right_name == right)
            or (c.left_name == right and c.right_name == left)
            for c in pairs
        )
    if not joined("Acme Robotics", "Acme Robotics"):
        failures.append("identical names must propose a merge")
    if not joined("Runway", "Runwayml"):
        failures.append("a body-name and its squashed domain-label must propose a merge")
    if joined("Anthropic", "OpenAI"):
        failures.append("two distinct real domains must never propose a merge")
    return failures


def run_resolution() -> int:
    failures = _failures()
    if failures:
        print("\nresolution eval FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nresolution eval PASS — windowing and duplicate detection hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_resolution())
