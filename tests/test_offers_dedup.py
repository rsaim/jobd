"""The offers page answers a per-company question.

A long hiring process does not arrive as one `application`. It arrives as
consecutive rows carved out of one mail chain -- nine tiling end-to-end for a
single hire on the reference record, two of them sharing a `thread_id` -- and
the visa paperwork after the acceptance lands as another. Every fragment
carries the offer, so a per-application list showed one offer three times
under three role titles, one of which was the person's own name mis-extracted.

The funnel already counts offers per company. These tests pin the ranking that
keeps the list agreeing with it.
"""

from __future__ import annotations

from jobd.services.dashboard import _STAGE_RANK


def rank(stage: str) -> int:
    return _STAGE_RANK.index(stage)


def test_settled_outcomes_outrank_a_bare_offer() -> None:
    """The kept row should say what became of the offer, not just that one
    existed -- so an accepted fragment wins over an open `offer` fragment."""
    assert rank("accepted") > rank("offer")
    assert rank("declined") > rank("offer")


def test_an_offer_outranks_the_rounds_that_led_to_it() -> None:
    for stage in ("applied", "recruiter_screen", "phone_screen", "technical", "onsite"):
        assert rank("offer") > rank(stage)


def test_a_rejection_does_not_outrank_the_offer() -> None:
    """A company can hold both a rejected fragment and an offered one (one
    team said no, another made the offer). On the offers page the offer is the
    answer; ranking the rejection higher would title the row 'rejected'."""
    assert rank("offer") > rank("rejected")
    assert rank("accepted") > rank("rejected")


def test_ranking_covers_every_stage_the_database_allows() -> None:
    """A stage missing here sorts NULL and could win a tie by accident."""
    from jobd.services.conversations import STAGES

    assert set(_STAGE_RANK) == set(STAGES)


def test_ordering_is_least_advanced_first() -> None:
    """`array_position(...) DESC` ranks a later entry higher, so the tuple
    must run least- to most-advanced. Reversing it would silently show the
    least informative fragment."""
    assert _STAGE_RANK[0] == "applied"
    assert _STAGE_RANK[-1] == "accepted"
