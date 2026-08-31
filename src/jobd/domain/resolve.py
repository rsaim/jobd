"""Entity-resolution rules. Pure functions; the database work is in services.

PRD §10 calls entity resolution the hardest sub-problem and explicitly permits
it to stay *deterministic plus review* in v1. This takes that permission: every
rule here is a rule you can read, and anything the rules cannot settle becomes a
new record rather than a confident merge.

The bias throughout is **split over merge**. An over-eager merge fuses two
hiring processes into one timeline that never happened, and unpicking it means
re-deriving from raw. An over-eager split leaves two company pages that a human
can join with one alias. Those are not the same mistake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Shared mailbox providers and intermediary infrastructure — personal
#: webmail, LinkedIn, ATS/scheduling hosts, recruiting agencies. Moved here
#: from `prefilter` when the hardcoded *verdict* lists were removed: this is
#: not a classification rule (nothing here decides job-related or not), it is
#: an identity guard for entity resolution and search planning. Three jobs:
#:
#: 1. A company must never be *inferred* from one of these domains — a
#:    recruiter replying from `@gmail.com` does not work for "Gmail"
#:    (live-caught: 13 real employers folded into one bogus "Gmail" company).
#: 2. The auto-teach policy (`jobd.domain.learning`) writes *address*-level
#:    negative rules for these instead of domain-level — one spammer at
#:    gmail.com must not silence every gmail correspondent.
#: 3. The scrape expansion loop never turns one into a search query — a
#:    learned gmail.com entity would pull the whole mailbox.
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
        # the domain must never become the inferred company.
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


def is_generic_domain(domain: str) -> bool:
    """Exact or subdomain match against :data:`GENERIC_DOMAINS`."""
    return any(
        domain == d or domain.endswith(f".{d}") for d in GENERIC_DOMAINS
    )


#: Corporate suffixes, dropped before comparing names. "Acme, Inc." and "Acme"
#: are the same company; keeping the suffix would make them two.
_SUFFIXES = (
    "inc",
    "inc.",
    "llc",
    "l.l.c",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "gmbh",
    "bv",
    "b.v",
    "ag",
    "sa",
    "plc",
    "pty",
    "holdings",
    "group",
    "technologies",
    "technology",
    "labs",
    "software",
)

#: Words carried by so many job titles that matching on them alone is noise.
_TITLE_STOPWORDS = frozenset(
    {
        "senior",
        "sr",
        "staff",
        "principal",
        "lead",
        "junior",
        "jr",
        "i",
        "ii",
        "iii",
        "iv",
        "the",
        "a",
        "an",
        "of",
        "and",
        "for",
        "at",
    }
)

#: How far outside an application's known window a message may fall and still
#: be counted as part of it. Hiring processes have gaps — a rejection can land
#: five weeks after the onsite — but a year later is a new attempt.
WINDOW = timedelta(days=45)

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalise_company(name: str) -> str:
    """A comparable form of a company name.

    Lowercased, punctuation stripped, corporate suffixes removed. Used only for
    *matching* — the canonical name stored on the record keeps its real
    capitalisation, because a company page reading "acme" is worse than one
    reading "Acme, Inc."
    """
    words = [w for w in _NON_WORD.split(name.lower()) if w]
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def normalise_domain(domain: str) -> str:
    """Strip scheme, `www.`, and trailing dots. Lowercased."""
    cleaned = domain.strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned).split("/")[0]
    cleaned = cleaned.removeprefix("www.").rstrip(".")
    return cleaned


def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        w for w in _NON_WORD.split(title.lower()) if w and w not in _TITLE_STOPWORDS
    )


def same_role(left: str | None, right: str | None) -> bool:
    """Whether two titles plausibly name the same job.

    Jaccard over significant tokens, seniority words removed. "Senior Backend
    Engineer" and "Backend Engineer" match; "Backend Engineer" and "Product
    Manager" do not.

    A missing title never matches a present one. Treating unknown as wildcard
    is how every untitled message ends up merged into whichever application
    happened to be first.
    """
    if not left or not right:
        return False
    a, b = _title_tokens(left), _title_tokens(right)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    return overlap >= 0.6


@dataclass(frozen=True, slots=True)
class ApplicationWindow:
    """The subset of an application this module needs to reason about."""

    id: object
    role_title: str | None
    started_at: datetime
    ended_at: datetime | None

    def contains(self, when: datetime) -> bool:
        """Whether a message at ``when`` plausibly belongs to this application."""
        if when < self.started_at - WINDOW:
            return False
        if self.ended_at is None:
            return True
        return when <= self.ended_at + WINDOW


def pick_application(
    candidates: list[ApplicationWindow],
    *,
    occurred_at: datetime,
    role_title: str | None,
) -> ApplicationWindow | None:
    """Choose which existing application a message belongs to, or None.

    None means "start a new one", and that is the deliberate default. P3 makes
    applications unbounded per company across years precisely so that the same
    role at the same company in 2021 and 2024 stays two records.

    Order of preference:

    1. Same role, and the message falls inside the window.
    2. An application with no role recorded, inside the window — the untitled
       "thanks for applying" that a later message will name.

    A role that matches but sits *outside* every window deliberately does not
    match. That is the 2021-and-2024 case, and merging it is the failure M3
    gate 3 exists to prevent.
    """
    within = [c for c in candidates if c.contains(occurred_at)]

    titled = [c for c in within if same_role(c.role_title, role_title)]
    if titled:
        return _closest(titled, occurred_at)

    untitled = [c for c in within if not c.role_title]
    if untitled:
        return _closest(untitled, occurred_at)

    return None


def _closest(
    candidates: list[ApplicationWindow], when: datetime
) -> ApplicationWindow:
    """Nearest start date. Ties break toward the later application."""
    return min(
        candidates,
        key=lambda c: (abs(c.started_at - when), -c.started_at.timestamp()),
    )
