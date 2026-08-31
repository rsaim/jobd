"""Exploration budget: re-examine negative rules (algorithm-improvements.md #3).

Negative rules ("silently hide real mail forever") have no mechanism to ever
be re-examined. Domains get re-purposed: a company's marketing domain becomes
their careers domain; a dead bank domain is reused by a startup.

This module provides an exploration budget — route a small fraction (2%) of
messages a negative rule would drop through the model anyway. If any come back
positive, the caller demotes the rule and the mail enters the record.

The decision is **deterministic per (rule, message)** — a hash, not a coin
flip — so re-deriving the record (I3) reproduces the same exploration sample
every time, and the "one paid call per thread" and "re-derive don't erase"
invariants hold. A fresh message still has an independent 2% chance, which is
what keeps the budget spread across the mailbox over time rather than pinned
to a fixed subset.

Result: measured, continuously-verified rule accuracy rather than "rules that
were right once." Directly addresses the one failure mode the design
philosophy cares about most: silently hiding real mail.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


EXPLORATION_RATE = 0.02  # 2% exploration budget


@dataclass(frozen=True, slots=True)
class ExplorationDecision:
    """Whether to explore (route through model) or apply the negative rule.

    `explore=True` means: ignore the negative rule for this message and route
    it through full classification. `rule_key` identifies which rule was
    explored (for tracking demotions).
    """

    explore: bool
    rule_key: str  # "domain:example.com" or "address:user@example.com"


def should_explore_negative(
    match_type: str,
    value: str,
    message_id: Any,
    exploration_rate: float = EXPLORATION_RATE,
) -> ExplorationDecision:
    """Decide whether to explore a message that hit a negative learned rule.

    With probability `exploration_rate` (default 2%), returns `explore=True`
    to route the message through the model rather than dropping it. Cheap
    (one md5), bounded (max 2% overhead), and directly prevents the "silently
    hide real mail forever" failure mode.

    Deterministic: hashing the rule key with the message id means the same
    message always yields the same decision, so `--reclassify` and concurrent
    partition workers cannot disagree about which messages were sampled.
    """
    rule_key = f"{match_type}:{value.lower()}"
    digest = hashlib.md5(f"{rule_key}:{message_id}".encode()).hexdigest()
    explore = (int(digest, 16) % 10_000) < int(exploration_rate * 10_000)
    return ExplorationDecision(explore=explore, rule_key=rule_key)
