"""Exploration budget: re-examine negative rules (algorithm-improvements.md #3).

Negative rules ("silently hide real mail forever") have no mechanism to ever
be re-examined. Domains get re-purposed: a company's marketing domain becomes
their careers domain; a dead bank domain is reused by a startup.

This module provides an exploration budget — route a small fraction (1-2%) of
messages a negative rule would drop through the model anyway. If any come back
positive, demote the rule and surface it for human review.

Result: Measured, continuously-verified rule accuracy rather than "rules that
were right once." Directly addresses the one failure mode the design
philosophy cares about most: silently hiding real mail.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


EXPLORATION_RATE = 0.02  # 2% exploration budget


@dataclass(frozen=True, slots=True)
class ExplorationDecision:
    """Whether to explore (route through model) or apply the negative rule.

    `explore=True` means: ignore the negative rule for this message and route
    it through full classification. This is the exploration budget in action.

    `rule_key` identifies which rule was explored (for tracking demotions).
    """

    explore: bool
    rule_key: str  # "domain:example.com" or "address:user@example.com"


def should_explore_negative(
    match_type: str, value: str, exploration_rate: float = EXPLORATION_RATE
) -> ExplorationDecision:
    """Decide whether to explore a message that hit a negative rule.

    With probability `exploration_rate` (default 2%), returns `explore=True`
    to route the message through the model rather than dropping it. This is
    cheap (one random() call), bounded (max 2% overhead), and directly
    prevents the "silently hide real mail forever" failure mode.

    Usage in prefilter or sweep:
        if verdict.status == "negative":
            decision = should_explore_negative(rule.match_type, rule.value)
            if decision.explore:
                # Route through model despite negative rule
                continue  # skip the negative-rule branch
            else:
                # Apply negative rule as usual
                mark_classified(...)
    """
    rule_key = f"{match_type}:{value.lower()}"
    explore = random.random() < exploration_rate
    return ExplorationDecision(explore=explore, rule_key=rule_key)


def on_exploration_positive(rule_key: str) -> dict[str, str]:
    """A message routed via exploration came back positive — the negative rule
    is wrong. Return metadata for logging/demotion.

    Caller's responsibility:
    1. Demote or delete the negative rule (UPDATE sender_rule SET verdict = NULL)
    2. Surface for human review (INSERT INTO review_queue with reason)
    3. Re-classify all messages currently hidden by this rule (backfill)

    This function just provides the structured data for that workflow.
    """
    match_type, value = rule_key.split(":", 1)
    return {
        "rule_key": rule_key,
        "match_type": match_type,
        "value": value,
        "reason": (
            f"Exploration budget found positive mail hidden by negative "
            f"rule {rule_key} — rule demoted for human review"
        ),
    }
