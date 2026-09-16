"""Active learning: impact-ranked review queue.

The review queue is the human's scarce attention. Every queue item has a
hidden fan-out value — resolving a domain that appears in 40 other pending
messages is worth far more than resolving a one-off sender.

This module provides the ranking logic that transforms FIFO triage into an
active-learning loop: the first thing a human sees is the decision that
clears the most residue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QueueRank:
    """One review queue item's impact score for ranking.

    `impact_score` is the composite rank: sibling count (how many other
    unclassified messages share this sender domain) weighted by whether the
    domain already has an `undecided` rule (bump priority — confirming it
    promotes to `positive` and unlocks zero-cost carry for all future mail).
    """

    review_id: Any  # UUID from review_queue.id
    message_id: Any  # UUID from message.id
    sender_domain: str
    sibling_count: int  # unclassified messages sharing this domain
    has_undecided_rule: bool
    impact_score: float


def rank_queue_items(
    conn: Any, limit: int = 100
) -> list[QueueRank]:
    """Rank pending review queue items by expected information gain.

    Returns up to `limit` queue items ordered by impact_score descending —
    highest-leverage decisions first. The query is cheap (a single aggregate
    over pending rows + a sender_rule lookup), no model calls.

    Impact scoring:
    - Base score = count of sibling unclassified messages sharing sender domain
    - 2x multiplier when domain has an existing `undecided` rule (promotion
      candidate — resolving it either promotes to `positive` for zero-cost
      carry, or surfaces a conflict for human review)
    """
    rows = conn.execute(
        """
        WITH queue_domains AS (
            -- Every pending review item with its sender domain (NULL domain
            -- folded to '' so an item with no sender_domain is still swept —
            -- just with zero impact, at the bottom of the ranking, never
            -- silently dropped from the queue).
            SELECT rq.id AS review_id,
                   rq.message_id,
                   coalesce(m.sender_domain, '') AS sender_domain,
                   -- Sibling count: how many OTHER unclassified messages
                   -- share this sender domain (including those not in the
                   -- queue yet — a domain with 50 pending/uncertain messages
                   -- is high-value even if only one is queued).
                   (SELECT count(*)
                     FROM message m2
                     WHERE m2.sender_domain = m.sender_domain
                       AND m.sender_domain IS NOT NULL
                       AND m2.company_id IS NULL
                       AND m2.classified_by IS NULL
                   ) AS sibling_count,
                   -- Existing undecided rule check: does this domain already
                   -- have an auto-taught undecided rule waiting for promotion?
                   EXISTS (
                       SELECT 1 FROM sender_rule sr
                       WHERE sr.match_type = 'domain'
                         AND lower(sr.value) = lower(m.sender_domain)
                         AND sr.verdict = 'undecided'
                   ) AS has_undecided_rule
            FROM review_queue rq
            JOIN message m ON m.id = rq.message_id
            WHERE rq.status = 'pending'
        )
        SELECT review_id, message_id, sender_domain, sibling_count,
               has_undecided_rule,
               -- Impact score: sibling count × 2 if undecided rule exists
               sibling_count * (CASE WHEN has_undecided_rule THEN 2.0 ELSE 1.0 END)
                   AS impact_score
        FROM queue_domains
        ORDER BY impact_score DESC, review_id
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    return [
        QueueRank(
            review_id=row[0],
            message_id=row[1],
            sender_domain=row[2],
            sibling_count=row[3],
            has_undecided_rule=row[4],
            impact_score=row[5],
        )
        for row in rows
    ]
