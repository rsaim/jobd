"""A provider with no model behind it.

Not a mock. It is a real `LLMProvider` that happens to reason with regular
expressions, and it exists for three reasons:

* **CI can run the whole pipeline.** Every stage from pre-filter to timeline is
  exercised on every PR without a model, a GPU, or a bill.
* **It is the eval baseline.** A learned extractor that cannot beat a page of
  regexes is not earning its cost, and without a baseline nobody notices.
* **It makes the outbound-call assertion meaningful.** `is_local` is true and
  there is no network code here at all, so a test that asserts "no cloud call
  happened" is asserting something structural.

Where the rules are sure — an explicit rejection, an ATS "we received your
application" — it labels "positive"; everywhere else it labels
"unclassified" (job-adjacent language with nothing concrete enough to trust
unchecked) or "negative" (no signal at all) and routes to review or drops it,
which is the right instinct for a system that cannot read.
"""

from __future__ import annotations

import re
from typing import Any

from jobd.domain.prefilter import ATS_DOMAINS, GENERIC_DOMAINS, domain_of

__all__ = ["GENERIC_DOMAINS", "RuleBasedProvider"]

#: (stage, patterns). First match wins, so order is precedence: an offer that
#: mentions the earlier phone screen is an offer. A hit here is always
#: label="positive" — every pattern below is concrete enough (a named stage
#: transition, not just job-adjacent vocabulary) to trust without review;
#: that used to be expressed as "confidence >= 0.78", a number that never
#: actually distinguished one pattern from another since they all cleared
#: the same 0.75 bar regardless of which one fired.
_STAGE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("offer", (r"offer letter", r"pleased to (?:extend|offer)", r"we'?d like to offer")),
    ("accepted", (r"accepted (?:the|your) offer", r"welcome to the team")),
    (
        "declined",
        (
            r"declin(?:e|ing|ed) (?:the|your) offer",
            r"turn(?:ing|ed)? down (?:the|your) offer",
            # Checked ahead of "rejected" below on purpose. "decided not to
            # move forward"/"not moving forward" is genuinely ambiguous
            # between a company rejecting a candidacy and a candidate
            # declining an offer — only the second names "the offer"
            # itself, which these two require. Real miss, live-caught: a
            # candidate's own "I've decided not to move forward with the
            # offer at this time" thank-you note matched "rejected" instead
            # — "rejected"'s patterns below are plain phrase matches with
            # no notion of who is speaking or what they're declining.
            r"not (?:be )?mov(?:e|ing) forward with (?:the|your) offer",
            r"decided not to (?:move|proceed|continue)[^.]{0,40}\boffer\b",
        ),
    ),
    (
        "rejected",
        (
            r"not (?:be )?mov(?:e|ing) forward",
            r"decided not to (?:move|proceed|continue)",
            r"pursu\w+ other candidates",
            r"unfortunately[^.]{0,60}(?:not|other candidates)",
            r"we will not be proceeding",
        ),
    ),
    ("withdrawn", (r"withdraw(?:ing|n)? (?:my|your) (?:application|candidacy)",)),
    ("onsite", (r"on-?site interview", r"final round", r"interview loop")),
    (
        "technical",
        (r"technical (?:interview|screen)", r"coding (?:challenge|exercise)",
         r"take[- ]home"),
    ),
    ("phone_screen", (r"phone screen", r"initial call with",)),
    (
        "recruiter_screen",
        (r"recruiter screen", r"intro(?:ductory)? call", r"quick chat about"),
    ),
    (
        "applied",
        (
            r"(?:we|i) received your application",
            r"thank(?:s| you) for applying",
            r"your application (?:for|has been)",
            r"application (?:was )?submitted",
        ),
    ),
)

_ROLE = re.compile(
    r"(?:for|the|as an?)\s+(?P<role>[A-Z][\w/+.-]*(?:\s+[\w/+.-]+){0,4}\s+"
    r"(?:Engineer|Developer|Scientist|Manager|Designer|Analyst|Architect|Lead))",
)
_COMPANY_AT = re.compile(
    r"\b(?:at|with|join(?:ing)?)\s+(?P<name>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,2})"
)


class RuleBasedProvider:
    """Deterministic extraction. Same input, same output, forever."""

    name = "rulebased"

    @property
    def is_local(self) -> bool:
        """True. There is no network code in this module."""
        return True

    def extract(
        self, prompt: str, schema: dict[str, Any], *, text: str
    ) -> dict[str, Any]:
        """Return a payload matching the extraction schema.

        `prompt` and `schema` are accepted and ignored — the signature is the
        port's, and honouring it is what lets this be swapped for a model
        without the caller noticing.
        """
        lowered = text.lower()

        # An out-of-office/auto-reply is never evidence of a stage — the
        # words in one can otherwise pattern-match almost anything, and
        # what it actually says is "I am away", not "the process moved".
        # Checked before every _STAGE_RULES pattern, not folded into one of
        # them, since it has to suppress a match regardless of which stage
        # bucket would otherwise have fired.
        auto_reply = bool(
            re.search(r"^(?:automatic reply|out of office|auto-?reply)\b", lowered)
            or "i'll be out of the office" in lowered
            or "i am currently out of the office" in lowered
        )

        stage: str | None = None
        if not auto_reply:
            for candidate, patterns in _STAGE_RULES:
                if any(re.search(p, lowered) for p in patterns):
                    stage = candidate
                    break

        # A "Welcome to <company>" thread paired with new-hire-paperwork
        # language is strong evidence an offer was made and accepted, even
        # when no message in it ever literally says "offer" or "accepted" —
        # every _STAGE_RULES pattern above requires one side to say so
        # outright. Real miss, live-caught: a real offer
        # negotiation never appears in the mailbox at all (conveyed by
        # phone/DocuSign); the only trace it ever happened is a "Re: Welcome
        # aboard, Alex!" thread asking to complete onboarding
        # paperwork, and it sat with `stage=None` forever. Requires BOTH the
        # greeting and a concrete onboarding term together — "welcome to X"
        # alone is indistinguishable from a marketing/newsletter welcome.
        if stage is None and not auto_reply:
            greeting = re.search(r"welcome to \w[\w &]{1,40}[,!]", lowered)
            onboarding_term = re.search(
                r"onboard|background check|\bi-9\b|\bw-4\b|benefits enrollment|"
                r"start date|new hire|corporate card|\bh-?1b\b|visa transfer|"
                r"\bbadge\b|paperwork",
                lowered,
            )
            if greeting and onboarding_term:
                stage = "accepted"

        sender = _first(r"^from:\s*(.+)$", text)
        company_domain = _company_domain(sender)
        company_name = _company_name(text, company_domain)
        ats_sender = _is_ats(sender)
        if not company_name and ats_sender:
            # An ATS confirmation's From display name is the company, not a
            # person — Lever/Greenhouse/Workday notifications send as
            # `"Acme Robotics" <no-reply@hire.lever.co>`. `_company_name`
            # can't use the domain here (ATS hosts are in GENERIC_DOMAINS on
            # purpose: a company must never be *inferred* from a shared ATS
            # domain), so this is the one remaining place the name is
            # readable — but only for a known ATS sender, so a stranger's
            # personal display name is never mistaken for a company.
            company_name = _display_name(sender)

        # Three buckets, in order of how sure a regex can be:
        # a named stage transition is concrete enough to trust outright;
        # job-adjacent vocabulary plus a company name is a real signal but
        # not concrete enough to skip a human; anything else has nothing
        # this provider can act on either way.
        if stage:
            label = "positive"
        elif company_name and _hiring_words(lowered):
            label = "unclassified"
        else:
            label = "negative"

        role = _ROLE.search(text)
        # The display name just used as the company is not a contact — an
        # ATS notification has no person behind it worth recording.
        contact_name = (
            None
            if (ats_sender and company_name)
            else _first(r"^from:\s*\"?([A-Z][\w.'-]+ [A-Z][\w.'-]+)", text)
        )
        return {
            "label": label,
            "company_name": company_name,
            "company_domain": company_domain,
            "role_title": role.group("role").strip() if role else None,
            "stage": stage,
            "contact_name": contact_name,
            "contact_email": _address(sender),
            "occurred_at": None,
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Not supported. Embeddings need a model; this provider has none.

        Raising beats returning zero vectors: silent zeros would populate the
        embedding column with values that make every semantic search return the
        same nearest neighbours, and nobody would know why.
        """
        raise NotImplementedError(
            "RuleBasedProvider cannot embed. Configure ollama or a cloud model."
        )


def _hiring_words(lowered: str) -> bool:
    return any(
        w in lowered
        for w in ("recruit", "interview", "candidate", "hiring", "role", "position")
    )


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _address(sender: str | None) -> str | None:
    if not sender:
        return None
    match = re.search(r"[\w.+-]+@[\w.-]+", sender)
    return match.group(0).lower() if match else None


def _is_ats(sender: str | None) -> bool:
    """Whether the sender's domain is a known ATS host. Suffix-matched, same
    as `ATS_DOMAINS`'s other consumer in `prefilter.py` — an ATS sends its
    notifications from many subdomains (`hire.lever.co`, `mail.ashbyhq.com`,
    a company's own `wd1.myworkdayjobs.com` tenant), not one fixed address."""
    address = _address(sender)
    if not address:
        return False
    domain = domain_of(address)
    return bool(domain) and any(
        domain == d or domain.endswith(f".{d}") for d in ATS_DOMAINS
    )


def _display_name(sender: str | None) -> str | None:
    """The header's display name — `"Acme Robotics" <jobs@hire.lever.co>` ->
    `Acme Robotics` — or None for a bare address with no name at all."""
    if not sender:
        return None
    match = re.match(r'^\s*"?([^"<]+?)"?\s*<', sender)
    return match.group(1).strip() or None if match else None


def _company_domain(sender: str | None) -> str | None:
    """The sender's domain, unless it is a mail provider or an ATS."""
    address = _address(sender)
    if not address:
        return None
    domain = domain_of(address)
    if not domain or any(domain.endswith(g) for g in GENERIC_DOMAINS):
        return None
    return domain


def _company_name(text: str, domain: str | None) -> str | None:
    match = _COMPANY_AT.search(text)
    if match:
        # Trailing punctuation comes from the sentence, not the name: "join
        # Acme Robotics." must not become a company called "Acme Robotics."
        return match.group("name").strip().rstrip(".,;:")
    if domain:
        # `careers.acme.co.uk` -> `Acme`. Crude, and the entity resolver will
        # merge it with whatever the real name turns out to be.
        parts = [p for p in domain.split(".") if p not in {"www", "mail", "careers"}]
        if parts:
            return parts[0].capitalize()
    return None
