"""The deterministic pre-filter (PRD P2, invariant I4).

Runs before any LLM call, which is what makes I4 true in practice rather than in
policy: a message this rejects is never sent anywhere.

**Metadata only.** No word or phrase matching against subject/body — that
judgment call belongs to the LLM, which can actually read. What this module
decides from: sender/recipient domains and addresses, Gmail's own labels,
RFC-standard bulk-mail headers, and the correspondent/thread/rule graph
(known_contact, thread_positive, learned) built from earlier confident
classifications. Every signal here is either a fixed fact (a domain is or
isn't an ATS) or a fact *proven* by an earlier classification — never a guess
from this message's own text.

**Fanout, not a lone verdict.** `learned` is how a human-confirmed fact about
one attribute (a domain, an address) applies to this message without asking
an extractor about it again — see `sender_rule` (migration 0008) and
`MessageRepository.bulk_resolve`/`bulk_negative` for the other half: applying
the same fact retroactively to every message already sitting in the backlog.

Every verdict carries its reasons. A filter you cannot interrogate is one
nobody can tune, and this one decides what the rest of the system is even
allowed to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Applicant tracking systems and recruiting platforms. Mail from these is
#: job-related essentially by definition, so one hit is enough on its own.
ATS_DOMAINS: frozenset[str] = frozenset(
    {
        "greenhouse.io",
        "us.greenhouse-mail.io",
        "lever.co",
        "hire.lever.co",
        "ashbyhq.com",
        "myworkday.com",
        "workday.com",
        "wd1.myworkdayjobs.com",
        "smartrecruiters.com",
        "taleo.net",
        "icims.com",
        "jobvite.com",
        "bamboohr.com",
        "breezy.hr",
        "workable.com",
        "workablemail.com",
        "recruitee.com",
        "teamtailor.com",
        "personio.de",
        "successfactors.com",
        "brassring.com",
        "hired.com",
        "triplebyte.com",
        "otta.com",
        "wellfound.com",
        "angel.co",
        "dover.com",
        "gem.com",
        "rippling.com",
        "ripplingmail.com",
        "paylocity.com",
        "eightfold.ai",
        "phenompeople.com",
        "avature.net",
        "jazzhr.com",
        "applytojob.com",
        "polymer.co",
        "pinpointhq.com",
    }
)

#: Personal mail providers and generic ATS/scheduling hosts. Shared with
#: `adapters.llm.rulebased` (a company must never be *inferred* from one of
#: these) — defined once here since it is downstream of this module, never the
#: reverse (domain/ imports nothing from adapters/).
GENERIC_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "fastmail.com",
        "aol.com",
        "linkedin.com",
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "myworkday.com",
        # Interview-scheduling platforms AND recruiting agencies
        # (SenderCategory.INTERVIEW_SCHEDULER / RECRUITING_AGENCY): pure
        # infrastructure or an intermediary, never the employer itself, so
        # the domain must never become the inferred company here either.
        #
        # This still holds for agencies even though many name no client at
        # all ("$5B Startup-hedge fund-NYC...") — `classify.py`'s
        # `agency_domain` fallback in `_resolve_company` handles that case
        # *deterministically*, sourced from the matched `sender_rule`
        # itself, not from anything `_company_domain`/`_company_name`
        # guessed. Excluding the domain here is what keeps that fallback
        # clean: without it, a garbage regex match (job-posting buzzwords
        # misread as a company name) or the bare agency domain leaks
        # through as `company_domain`/`company_name` first, and
        # `_resolve_company`'s `domain is None and not extraction.company_name`
        # check never sees the agency case at all. Confirmed by testing —
        # jkpartner.com mail without this exclusion resolved a company
        # literally named "Python", lifted from "C#/Python Core Developer"
        # in the subject line.
        "goodtime.io",
        "interviews.modernloop.io",
        "jkpartner.com",
        "mavenpartnership.com",
        "alldus.com",
        "cpi-search.com",
        "acaciarecruits.com",
        "selbyjennings.com",
        "topfunneltalent.com",
        "brisktalent.com",
        "intelletec.com",
        "emaago.com",
    }
)

#: Banking, brokerage, and payment domains. Content is sensitive and never
#: job-related — an unconditional negative, not a down-weight.
BANK_DOMAINS: frozenset[str] = frozenset(
    {
        "chase.com",
        "bankofamerica.com",
        "wellsfargo.com",
        "citibank.com",
        "citi.com",
        "capitalone.com",
        "americanexpress.com",
        "discover.com",
        "usbank.com",
        "schwab.com",
        "fidelity.com",
        "vanguard.com",
        "paypal.com",
        "venmo.com",
        "ally.com",
        "chime.com",
        "sofi.com",
        "robinhood.com",
    }
)

#: Gmail's own bulk-mail categories (`labelIds` on the API object, comma-joined
#: by the source before this ever sees it — see adapters/gmail/source.py).
#: Google already ran a classifier on every message; using its output here is
#: free.
NOISE_LABELS: frozenset[str] = frozenset(
    {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
)


class SenderCategory:
    """Named groups of senders that share one verdict (migration 0009).

    Each member is a real, discovered group — not a speculative taxonomy.
    Add one only when a second sender needs the same verdict as a first
    already-learned one; a group of one is just `sender_rule` with no
    category. Plain string constants, not `enum.Enum`: the value *is* what
    gets stored in `sender_category.category` and matched against, and a
    bare string needs no adaptation going into or reading back from psycopg.
    """

    #: LinkedIn's own algorithmic job-match digest — "software engineer":
    #: Arena - Software Engineer... — not a recruiter, a search result email.
    LINKEDIN_JOB_ALERTS = "LINKEDIN_JOB_ALERTS"

    #: LinkedIn platform noise with no per-recruiter content at all: group
    #: digests, editorial newsletters, security alerts, "N others are hiring"
    #: spam. Distinct from LINKEDIN_JOB_ALERTS only for a readable taxonomy —
    #: same verdict, different reason.
    LINKEDIN_DIGEST = "LINKEDIN_DIGEST"

    #: The opposite case: real per-recruiter content routed through a shared
    #: LinkedIn address (InMail replies, message-thread digests) — content
    #: no domain/address rule can resolve on its own. Pinned UNDECIDED so it
    #: is never swept into NEGATIVE by the bulk-header/label tier just
    #: because LinkedIn's platform sends it through bulk-mail infrastructure.
    LINKEDIN_CONVERSATIONS = "LINKEDIN_CONVERSATIONS"

    #: amazon.com is genuinely mixed — real recruiter mail (personal-name
    #: local parts like `etthermi@amazon.com`) shares the domain with retail
    #: order/shipping/marketplace noise, so no domain-level rule is safe here.
    #: This tags the *specific*, individually-confirmed retail local parts
    #: only (`order-update@`, `auto-confirm@`, ...) — an address not in this
    #: group (a real name) is deliberately left with no rule at all, routed
    #: to the LLM same as before any of this existed.
    AMAZON_RETAIL = "AMAZON_RETAIL"

    #: Third-party recruiting/staffing firms. One domain fields mail about
    #: many different client companies — sometimes naming one, often not
    #: (see classify.py's agency-domain fallback in `_resolve_company`).
    #: Pinned UNDECIDED, not NEGATIVE: genuine recruiter content, just not
    #: company-inferable from the domain alone.
    RECRUITING_AGENCY = "RECRUITING_AGENCY"

    #: Interview-scheduling infrastructure (Goodtime, ModernLoop). Pure
    #: platform, like an ATS — the employer is always named elsewhere in
    #: the message, so unlike RECRUITING_AGENCY these domains stay in
    #: GENERIC_DOMAINS and get no fallback-to-self logic.
    INTERVIEW_SCHEDULER = "INTERVIEW_SCHEDULER"


#: POSITIVE  — deterministically job-related. No model call needed to decide
#:             *that*; a fast, free extractor can still be asked *what* (see
#:             classify.py's routing).
#: NEGATIVE  — deterministically not job-related. Never reaches a model.
#: UNDECIDED — everything else. This is the pool that actually costs a call —
#:             deliberately the default: metadata alone often cannot say no,
#:             and only the LLM reads the actual content.
Status = Literal["positive", "negative", "undecided"]

#: A rule learned from one human decision, fanned out to every message that
#: shares the attribute. See `sender_rule` (migration 0008). "undecided" is
#: an explicit override (migration 0010): "we looked at this group, it
#: genuinely needs the LLM" — protects a correspondent from being silently
#: swept into NEGATIVE by the generic bulk-header/Gmail-label heuristics
#: below it (real recruiter platforms send through bulk-mail infrastructure
#: too), while still yielding to a stronger *proven* per-message signal
#: above it (known_contact, thread_positive, an ATS-domain hit).
LearnedVerdict = Literal["positive", "negative", "undecided"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why a message was kept, dropped, or sent onward undecided."""

    status: Status
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_candidate(self) -> bool:
        """True unless deterministically negative."""
        return self.status != "negative"

    def __str__(self) -> str:
        reasons = ", ".join(self.reasons) or "no signals"
        return f"{self.status}: {reasons}"


def domain_of(address: str) -> str:
    """The domain part of an address, lowercased. Empty when there isn't one."""
    _, _, domain = address.partition("@")
    return domain.strip().strip(">").lower()


def _matches_domain(domain: str, known: frozenset[str]) -> str | None:
    """Exact or subdomain match. `mail.greenhouse.io` matches `greenhouse.io`."""
    for candidate in known:
        if domain == candidate or domain.endswith(f".{candidate}"):
            return candidate
    return None


#: Local parts that mark an address as automated rather than a person.
NO_REPLY_LOCAL_PARTS: frozenset[str] = frozenset(
    {"no-reply", "noreply", "donotreply", "do-not-reply", "notifications"}
)


def is_automated(address: str) -> str | None:
    """The reason an address is a system rather than a person, or None.

    The dashboard's people panel needs this: a contact row for
    `no-reply@ashbyhq.com` is a pipe the application ran through, not somebody
    who talked to you, and mixing the two makes the panel useless. Public
    because it is read outside this module, and here rather than in the
    dashboard so the ATS list has exactly one definition — the same one
    :func:`score` filters on.
    """
    domain = domain_of(address)
    hit = _matches_domain(domain, ATS_DOMAINS) if domain else None
    if hit:
        return f"ats:{hit}"
    local = address.partition("@")[0].strip().strip("<").lower()
    if local in NO_REPLY_LOCAL_PARTS or local.startswith(("no-reply", "noreply")):
        return "no-reply"
    # LinkedIn's shared reply addresses carry per-recruiter content but are
    # not a person's own mailbox — see SenderCategory.LINKEDIN_CONVERSATIONS.
    if "hit-reply@linkedin.com" in address.lower():
        return "linkedin-relay"
    return None


def score(
    *,
    sender: str = "",
    recipients: str = "",
    known_contact: bool = False,
    thread_positive: bool = False,
    learned: LearnedVerdict | None = None,
    gmail_labels: str = "",
    has_list_unsubscribe: bool = False,
) -> Verdict:
    """Classify one message from metadata alone — no subject/body reading.

    Args:
        sender: From header, address or full form.
        recipients: To/Cc, joined. Used only for ATS domain hits — a reply *to*
            an ATS address is as job-related as one from it.
        known_contact: True when one of the correspondents is already a contact
            in the record — i.e. some *other* message from/to this exact
            address was, at some point, confidently recorded as job-related.
            Once a recruiter's address is confirmed, every future message
            involving them is job-related by construction.
        thread_positive: True when some *other* message in the same
            conversation chain (Gmail thread) already resolved to a company.
        learned: A human-confirmed rule for this sender's domain or address
            (see `sender_rule`, migration 0008) — the fanout mechanism. Takes
            precedence over everything below it: a human decision about an
            attribute outranks a hardcoded domain list.
        gmail_labels: Comma-joined `labelIds` from the source (empty for a
            source that has none). Checked against :data:`NOISE_LABELS`.
        has_list_unsubscribe: True if the message carries a `List-Unsubscribe`
            header or `Precedence: bulk`/`Precedence: list` — the RFC-standard
            bulk-mail markers. Recorded but no longer decisive on its own
            (see `status` below) — plenty of legitimate transactional mail
            carries this header for compliance reasons without being bulk
            marketing. Live-caught: a DocuSign "you have a document to sign"
            notification for a real, signed job offer had
            one and was silently dropped to `negative` before this fix —
            never reached an extractor, the offer never entered the record.

    Returns a :class:`Verdict` carrying the reasons, so a surprising drop can be
    explained rather than guessed at.
    """
    reasons: list[str] = []

    ats_hit = False
    for address in (sender, recipients):
        has_at = "@" in address
        hit = _matches_domain(domain_of(address), ATS_DOMAINS) if has_at else None
        if hit is None:
            hit = next((d for d in ATS_DOMAINS if d in address.lower()), None)
        if hit:
            ats_hit = True
            reasons.append(f"ats-domain:{hit}")
            break

    bank_hit = _matches_domain(domain_of(sender), BANK_DOMAINS)
    if bank_hit:
        reasons.append(f"bank-domain:{bank_hit}")

    if known_contact:
        reasons.append("known-contact")

    if thread_positive:
        reasons.append("thread-positive")

    if learned:
        reasons.append(f"learned:{learned}")

    hit_labels = {lbl for lbl in gmail_labels.split(",") if lbl} & NOISE_LABELS
    if hit_labels:
        reasons.append(f"gmail-label:{'|'.join(sorted(hit_labels))}")

    if has_list_unsubscribe:
        reasons.append("bulk-header")

    status: Status
    if learned == "positive" or ats_hit or known_contact or thread_positive:
        # A human decision about this attribute, an ATS-domain hit, a proven
        # correspondent, or a proven thread — every one of these is a fact
        # already established, not a guess from this message's own content.
        status = "positive"
    elif learned == "undecided":
        # The override: a human already decided this group needs the LLM,
        # so it must not fall into the generic bulk-header/label negative
        # tier below just because the platform it rides on also carries one.
        status = "undecided"
    elif learned == "negative" or bank_hit or hit_labels:
        status = "negative"
    else:
        status = "undecided"

    return Verdict(status=status, reasons=tuple(reasons))
