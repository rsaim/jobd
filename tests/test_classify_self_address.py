"""The account's own address must never act as a learned rule.

Live-caught twice: a `<own-address> -> undecided` rule matched on the
*recipient* side of every inbound message, outranked both negative domain
rules and Gmail's noise labels in `prefilter.score`, and routed the whole
mailbox to the paid extractor. `verify.py` and the web teach form already
refuse to *write* such a rule; these tests pin the read side, which is what
actually decides every message.
"""

from __future__ import annotations

from uuid import uuid4

from jobd.domain import prefilter
from jobd.domain.record import SenderRule
from jobd.services.classify import _match_rule

OWN = "someone@gmail.com"


def _rule(match_type: str, value: str, verdict: str) -> SenderRule:
    return SenderRule(
        id=uuid4(), match_type=match_type, value=value, verdict=verdict, source="auto"
    )


def test_self_address_rule_is_ignored() -> None:
    """A rule on the user's own address must not decide someone else's mail."""
    rules = [_rule("address", OWN, "undecided")]
    verdict, *_ = _match_rule(
        rules, "Paytm <mail@email.paytmmoney.com>", f"Me <{OWN}>", own_address=OWN
    )
    assert verdict is None, "own-address rule must not match"


def test_self_address_does_not_shadow_negative_domain_rule() -> None:
    """The real regression: `undecided` on self outranked a negative domain
    rule, so a known-noise sender still paid for a model call."""
    rules = [
        _rule("address", OWN, "undecided"),
        _rule("domain", "email.paytmmoney.com", "negative"),
    ]
    verdict, _, _, _, rule = _match_rule(
        rules, "Paytm <mail@email.paytmmoney.com>", f"Me <{OWN}>", own_address=OWN
    )
    assert verdict == "negative"
    assert rule is not None and rule.value == "email.paytmmoney.com"

    scored = prefilter.score(
        sender="Paytm <mail@email.paytmmoney.com>",
        recipients=f"Me <{OWN}>",
        learned=verdict,
        gmail_labels="CATEGORY_PROMOTIONS",
        has_list_unsubscribe=True,
    )
    assert scored.status == "negative", "must be filtered free, before any LLM call"


def test_self_address_does_not_shadow_gmail_noise_label() -> None:
    """With no domain rule at all, the noise label must still win — the
    self-rule previously forced `undecided` and defeated it."""
    rules = [_rule("address", OWN, "undecided")]
    verdict, *_ = _match_rule(
        rules, "Groupon <noreply@r.groupon.com>", f"Me <{OWN}>", own_address=OWN
    )
    scored = prefilter.score(
        sender="Groupon <noreply@r.groupon.com>",
        recipients=f"Me <{OWN}>",
        learned=verdict,
        gmail_labels="CATEGORY_PROMOTIONS",
    )
    assert scored.status == "negative"


def test_rules_about_other_addresses_still_match() -> None:
    """The guard is surgical: only the account's own address is skipped."""
    rules = [_rule("address", "recruiter@acme.com", "positive")]
    verdict, *_ = _match_rule(
        rules, "Recruiter <recruiter@acme.com>", f"Me <{OWN}>", own_address=OWN
    )
    assert verdict == "positive"


def test_recipient_side_rules_still_match() -> None:
    """Fanout via recipients is intentional for *other* addresses."""
    rules = [_rule("address", "jobs@acme.com", "negative")]
    verdict, *_ = _match_rule(
        rules, "Someone <x@example.org>", "Jobs <jobs@acme.com>", own_address=OWN
    )
    assert verdict == "negative"


def test_no_own_address_configured_is_backwards_compatible() -> None:
    """Callers that pass nothing keep the old behaviour."""
    rules = [_rule("address", OWN, "undecided")]
    verdict, *_ = _match_rule(rules, f"Me <{OWN}>", "")
    assert verdict == "undecided"
