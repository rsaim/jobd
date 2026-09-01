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
  A second confident extraction from the same domain resolving to the *same*
  company promotes the rule to `positive`, which unlocks classify's zero-cost
  rule-carry path. This two-step earn is what the old ATS list short-circuited
  by fiat. Promotion is additionally gated on two conditions that the two-step
  earn alone would miss (algorithm-improvements.md #1): the two extractions
  must resolve to the *same entity* (a domain that answered to two different
  companies never promotes — it would fuse two hiring processes into one
  timeline), and a recruiting-agency domain never promotes at all (an agency
  fields many clients by construction).
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
    to skip re-journaling a domain they already journaled."""

    match_type: Literal["domain", "address"]
    value: str
    verdict: Literal["positive", "negative", "undecided"]
    promotion: bool = False


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


def known_companies(rules: list) -> dict[str, tuple[object, str]]:
    """Rule key → `(company_id, source)` for domain rules that pin a company,
    keyed identically to `known_verdicts`. The promotion check needs both: a
    domain that resolved to two different companies must not be promoted to
    `positive`, because rule-carry would then pin every future message from
    that domain to whichever company happens to sit on the rule row — the
    "two hiring processes fused into one timeline" failure the pipeline is
    built to refuse. And a `source="human"` rule must never be promoted by
    the machine at all (AGENTS.md: machine-derived rules are never `positive`
    over a human decision) — the verdict map alone cannot tell, so the source
    rides along here. Address rules carry no company identity (only domain
    rules feed the promotion path)."""
    return {
        _key(r.match_type, r.value): (r.company_id, getattr(r, "source", "human"))
        for r in rules
        if r.match_type == "domain" and getattr(r, "company_id", None) is not None
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
    company_key: object | None,
    company_kind: str | None,
    known: Mapping[str, str],
    known_company: Mapping[str, tuple[object, str]] | None,
) -> Teach | None:
    """A `label="positive"` extraction earned the record; what does its
    company domain earn?

    First sighting → `undecided` (see module docstring). A standing
    auto-taught `undecided` for the same domain → promote to `positive` ONLY
    if this extraction resolved to the same company as the first sighting
    (`company_key` vs the pinned `known_company` entry). Two independent
    confident extractions agreeing on the *entity* is the bar for skipping
    the model entirely — so an `undecided` with no pinned company is a
    first-sighting-equivalent, not agreement, and never promotes on one
    extraction. A `source="human"` rule never promotes either: the machine
    must not turn a human's `undecided` into an auto `positive` (AGENTS.md).
    Any other standing verdict → nothing.

    `company_kind == "agency"` never promotes: an agency domain fields many
    client employers by construction, and a domain-wide positive rule would
    fuse them all into one timeline. Same split-bias contract the entity check
    enforces (algorithm-improvements.md #1).
    """
    domain = company_domain.lower()
    if not domain or is_generic_domain(domain):
        # A shared provider must never be taught as a company's identity —
        # the same guard `_resolve_company` applies before minting a company.
        return None
    if company_kind == "agency":
        return None

    current = known.get(_key("domain", domain))
    if current is None:
        return Teach("domain", domain, "undecided")
    if current == "undecided":
        pinned = (known_company or {}).get(_key("domain", domain))
        if pinned is None:
            # No pinned company to agree with (a human-taught or distilled
            # `undecided` carries none) — this extraction is the first
            # sighting of an entity, not a second one agreeing with it.
            return None
        pinned_company, pinned_source = pinned
        if pinned_source == "human":
            # A human decided `undecided`; the machine never overrides it
            # with an auto `positive` — a contradiction is a human's to
            # resolve (module docstring, AGENTS.md).
            return None
        if company_key is None or pinned_company != company_key:
            # The two confident extractions resolved to different companies —
            # a shared corporate domain or a mis-taught agency. Do not
            # promote; the split-bias contract refuses to fuse them.
            return None
        return Teach("domain", domain, "positive", promotion=True)
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
