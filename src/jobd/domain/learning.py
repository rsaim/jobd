"""The online-learning policy: what one classification outcome teaches.

Pure functions over plain data — no database, no I/O. `classify.py` applies
the returned actions to the `sender_rule` table at run time; `evals/` replays
the same functions against the gold corpus to prove the loop converges (a
second pass over the same senders must cost fewer model calls, at no accuracy
loss). One definition, two consumers, so the eval can never drift from what
production actually learns.

The policy replaced the hardcoded domain lists (`ATS_DOMAINS`,
`BANK_DOMAINS`, and the verdict half of `GENERIC_DOMAINS`) that used to live
in `prefilter.py`:

* **Positives converge in two steps.** A first confident extraction teaches
  `undecided` — enough to protect the sender from the bulk-header/label
  negative sweep, not enough to skip the model (one message is one datapoint).
  A second confident extraction from the same domain resolving to the same
  company promotes the rule to `positive`, which unlocks classify's zero-cost
  rule-carry path. This two-step earn is what the old ATS list short-circuited
  by fiat. **CRITICAL FIX (algorithm-improvements.md #1):** Promotion now
  checks that both extractions resolve to the SAME company_id — preventing
  entity fusion where recruiting-agency domains or multi-role corporate
  domains silently fuse two different hiring processes into one timeline.
* **Negatives teach immediately.** A model "not job-related" verdict writes a
  negative rule on the spot — that sender never costs a model call again.
  This is what `BANK_DOMAINS` used to hardcode for eighteen banks; now any
  noisy sender earns it in one call. The blast radius is bounded by scope:
  corporate domains get a *domain* rule, shared mailbox providers
  (`resolve.GENERIC_DOMAINS` — gmail.com and friends) get an *address* rule,
  so one spammer on webmail never silences every other correspondent there.
* **Existing rules are never overwritten by the policy.** A covering rule of
  any verdict means no action: contradictions between the model and an
  earlier decision (especially a human one, `source="human"`) are a human's
  to resolve, not the model's to flip silently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from jobd.domain.resolve import is_generic_domain


@dataclass(frozen=True, slots=True)
class Teach:
    """One rule write the policy decided on. `promotion` marks an
    undecided→positive upgrade rather than a first sighting — callers use it
    to skip re-journaling a domain they already journaled.

    `company_id` is carried for first sightings (undecided) so the repository
    can record which company this domain was first associated with — required
    for the promotion-path entity-fusion fix (algorithm-improvements.md #1).
    """

    match_type: Literal["domain", "address"]
    value: str
    verdict: Literal["positive", "negative", "undecided"]
    promotion: bool = False
    company_id: str | None = None


def _key(match_type: str, value: str) -> str:
    return f"{match_type}:{value.lower()}"


def known_verdicts(
    rules: list,  # list[SenderRule] — duck-typed to keep this module pure
) -> dict[str, str]:
    """The in-memory index the policy consults: rule key → verdict. Seeded
    from the loaded rule set at batch start, grown in place as teaches are
    applied — same lifecycle as classify's old `taught_domains` set."""
    return {
        _key(r.match_type, r.value): (r.verdict or "undecided") for r in rules
    }


def known_companies(
    rules: list,  # list[SenderRule] — duck-typed to keep this module pure
) -> dict[str, str | None]:
    """The in-memory index of domain → company_id for promotion checks.
    Only domain rules carry company_id; address rules don't (they're specific
    to one sender on a shared provider, not a whole company domain).

    Built from the same rule set as known_verdicts — same lifecycle."""
    return {
        r.value.lower(): r.company_id
        for r in rules
        if r.match_type == "domain" and r.company_id is not None
    }


def covering_verdict(known: Mapping[str, str], address: str) -> str | None:
    """The verdict of any rule covering `address` — exact address rule first
    (the more specific match), then domain rules by suffix, same semantics as
    classify's `_match_rule`."""
    lowered = address.lower()
    hit = known.get(_key("address", lowered))
    if hit is not None:
        return hit
    _, _, domain = lowered.partition("@")
    domain = domain.strip().strip(">")
    if not domain:
        return None
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        hit = known.get(_key("domain", suffix))
        if hit is not None:
            return hit
    return None


def on_confident_positive(
    *,
    company_domain: str,
    company_id: str | None,
    company_kind: str | None,
    known: Mapping[str, str],
    known_companies: Mapping[str, str | None],
) -> Teach | None:
    """A `label="positive"` extraction earned the record; what does its
    company domain earn?

    First sighting → `undecided` (see module docstring). A standing
    auto-taught `undecided` for the same domain → promote to `positive` ONLY
    if the company_id matches the first sighting's company. Two independent
    confident extractions resolving to the SAME company is the bar for
    skipping the model entirely. Any other standing verdict → nothing.

    If company_id disagrees or company_kind is "agency", do not promote —
    the split-bias contract demands we never silently fuse two different
    companies into one timeline.
    """
    domain = company_domain.lower()
    if not domain or is_generic_domain(domain):
        # A shared provider must never be taught as a company's identity —
        # the same guard `_resolve_company` applies before minting a company.
        return None
    # Agency domains must never be promoted to positive — each client hiring
    # process is a different company, and a domain-wide positive rule would
    # fuse them all into one timeline (algorithm-improvements.md #1).
    if company_kind == "agency":
        return None

    current = known.get(_key("domain", domain))
    if current is None:
        # First sighting: teach undecided and record this company_id so future
        # promotions can verify agreement (algorithm-improvements.md #1).
        return Teach("domain", domain, "undecided", company_id=company_id)
    if current == "undecided":
        # Second sighting: only promote if it resolves to the same company as
        # the first. Check known_companies mapping (domain → company_id).
        first_company = known_companies.get(domain)
        if first_company is not None and first_company == company_id:
            return Teach("domain", domain, "positive", promotion=True)
        # Disagreement: the two extractions resolved to different companies.
        # Do not promote — fall back to message-level classification or an
        # address-scoped rule (future work: surface as a conflict for human).
        return None
    return None


def on_model_negative(
    *, sender_address: str, known: Mapping[str, str]
) -> Teach | None:
    """The model read the message and said not job-related; what does the
    sender earn?

    An immediate negative rule — domain-scoped for corporate senders,
    address-scoped for shared mailbox providers (job #2 in
    `GENERIC_DOMAINS`'s docstring). No action when any rule already covers
    the sender: the standing decision wins, and a disagreement is surfaced
    for a human (see `sweep_review_queue`'s `rejected_domains`), not flipped.
    """
    lowered = sender_address.lower().strip().strip("<>")
    if "@" not in lowered:
        return None
    if covering_verdict(known, lowered) is not None:
        return None
    _, _, domain = lowered.partition("@")
    if not domain:
        return None
    if is_generic_domain(domain):
        return Teach("address", lowered, "negative")
    return Teach("domain", domain, "negative")
