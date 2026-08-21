"""The record (PRD P3): Company → Application → StageEvent → Message → Contact.

Plain frozen dataclasses. No ORM base class, no persistence knowledge, no
`save()` — the repositories in `adapters/postgres` own that, and the core stays
something you can construct in a test without a database.

``id`` is ``None`` until a repository has stored the object. Every ``add``
returns a new instance with the id filled in rather than mutating in place,
because these are frozen and because a half-stored object with a real id is a
thing worth being unable to represent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

Channel = Literal["email", "linkedin"]
Direction = Literal["inbound", "outbound"]
AliasSource = Literal["llm", "manual", "domain", "import"]
ApplicationSource = Literal["inferred", "manual", "import"]
#: 'declined' is not 'withdrawn': withdrawn is the candidate leaving a
#: process before any decision was made; declined is the candidate turning
#: down an offer that was actually extended — the process succeeded and they
#: still said no. Distinct from 'rejected' (the company said no) in the
#: other direction. Migration 0017.
Outcome = Literal["offer", "accepted", "rejected", "withdrawn", "declined"]
#: Whether a company ever employs the user, or only introduces them to one.
#: An agency shares every other field's semantics with a real employer (a
#: name, a domain, first/last seen) — this is the one thing that's
#: different, so it is a discriminator column, not a separate table
#: (migration 0012).
CompanyKind = Literal["employer", "agency"]
#: How a sender_rule came to exist: a human's `jobd learn`, or `_record`'s
#: online-learning auto-teach (migration 0014). What makes "rules learned"
#: on the dashboard a real, filterable stat instead of one undifferentiated
#: count.
RuleSource = Literal["human", "auto"]

#: The stage taxonomy, resolving the PRD §10 open question.
#:
#: ``ghosted`` is absent, and that is the answer: ghosting is the *absence* of
#: an event. Nobody sends the mail that means "we have stopped replying", so
#: there is no evidence message to link it to — and every StageEvent is
#: evidence-linked by construction (G2). It is derived at query time from
#: last-touch recency plus direction, the same pair that drives whose-turn-is-it
#: (P4.1). See :func:`is_ghosted`.
Stage = Literal[
    "applied",
    "recruiter_screen",
    "phone_screen",
    "technical",
    "onsite",
    "offer",
    "rejected",
    "withdrawn",
    "accepted",
    "declined",
]

#: Terminal stages. An application that reached one of these is finished, and
#: silence afterwards is an ending rather than a ghosting.
TERMINAL_STAGES: frozenset[str] = frozenset(
    {"rejected", "withdrawn", "accepted", "declined"}
)


@dataclass(frozen=True, slots=True)
class Company:
    """An organization discovered in the user's communications.

    Never user-created (P4.1): companies emerge from classification, so there is
    no "add company" flow to keep consistent with the mail.
    """

    canonical_name: str
    first_seen_at: datetime
    last_seen_at: datetime
    domain: str | None = None
    kind: CompanyKind = "employer"
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CompanyAlias:
    """Another name the same company goes by."""

    company_id: UUID
    alias: str
    source: AliasSource = "llm"
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Contact:
    """A person, across every channel they appear on."""

    display_name: str | None = None
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ContactIdentity:
    """One address or handle belonging to a contact.

    The cross-channel merge lives here: the same recruiter mailing two of the
    user's addresses and messaging on LinkedIn is three identities, one contact.
    """

    contact_id: UUID
    channel: Channel
    identifier: str
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Application:
    """One attempt at one role. Unbounded per company, across years (P3)."""

    company_id: UUID
    started_at: datetime
    role_title: str | None = None
    ended_at: datetime | None = None
    outcome: Outcome | None = None
    source: ApplicationSource = "inferred"
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Message:
    """One communication, already written to raw storage.

    ``storage_key`` is the content hash from :mod:`jobd.domain.keys`. It is the
    join back to the source of truth, and its uniqueness is what makes a second
    ingest collide instead of duplicating (I2).
    """

    storage_key: str
    channel: Channel
    account: str
    direction: Direction
    sent_at: datetime
    external_id: str | None = None
    # Gmail's threadId (or another source's native conversation-chain id).
    # None for sources without one. See migration 0006 for why this exists —
    # it is the "same conversation" half of the known-contact filter.
    thread_id: str | None = None
    # The correspondent graph (migration 0007): who sent it, and who an
    # outbound reply went to. Queryable in SQL rather than re-derived from S3
    # on every lookup — what makes bulk rule propagation ("this address is a
    # Acme recruiter, resolve every message touching it") a single UPDATE
    # instead of a mailbox-wide re-fetch.
    sender_address: str | None = None
    sender_domain: str | None = None
    recipient_addresses: tuple[str, ...] = ()
    subject: str | None = None
    body_text: str = ""
    company_id: UUID | None = None
    application_id: UUID | None = None
    contact_id: UUID | None = None
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StageEvent:
    """A stage transition, with the message that proves it.

    ``evidence_message_id`` is required, not optional. G2 promises every claim
    links to its evidence; a nullable field would make that promise a habit.
    """

    application_id: UUID
    stage: Stage
    occurred_at: datetime
    evidence_message_id: UUID
    confidence: float | None = None
    extracted_by: str = "manual"
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SenderRule:
    """A learned, human-confirmed fact about a domain or address.

    The persisted half of fanout classification (see migration 0008): once
    one email teaches us "acmecorp.com is Acme's recruiting domain" or "this
    newsletter platform is never job-related", every message touching that
    attribute — past and future — inherits the verdict without asking an
    extractor again.

    ``verdict``/``company_id`` are the rule's own, direct answer. ``category``
    (migration 0009) is the alternative: tag this attribute as belonging to a
    named group (`SenderCategory.LINKEDIN_JOB_ALERTS`, say) and let
    :class:`SenderCategory`'s own row in `sender_category` supply the verdict
    instead — one row a whole group's worth of senders all defer to. A rule
    always carries at least one of the two (DB-enforced); when both are
    loaded from storage, the category wins (see `SenderRuleRepository.all`).
    """

    match_type: Literal["domain", "address"]
    value: str
    verdict: Literal["positive", "negative", "undecided"] | None = None
    category: str | None = None
    company_id: UUID | None = None
    source: RuleSource = "human"
    id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MessageCompany:
    """The secondary company/agency link on a message (migration 0013).

    ``message.company_id`` (above) is the primary link and stays untouched —
    every existing query still reads it directly. This is purely additive:
    the one case a single FK can't represent, a message that names both a
    recruiting agency *and* the specific client company it concerns.
    """

    message_id: UUID
    company_id: UUID
    role: Literal["employer", "agency"]


@dataclass(frozen=True, slots=True)
class CompanyVerification:
    """One independent LLM audit pass over a company's whole message chain
    (migration 0015, `services/verify.py`) — a finding, not a mutation. See
    that module's docstring for why nothing here writes to `company` or
    `sender_rule` on its own.

    `suggested_rules`/`raw_response` are kept whole rather than normalised —
    same reasoning as `review_queue`'s `extraction` column (the fields that
    explain *why* a rule was proposed are exactly what normalising would
    drop).
    """

    #: Nullable: a company confirmed not job-related is deleted outright
    #: (`services/verify.py`), and this row survives it (migration 0016,
    #: ON DELETE SET NULL) — the verified_* columns below already capture
    #: enough to stay legible with no company row behind them at all.
    company_id: UUID | None
    model: str
    message_count: int
    raw_response: dict[str, Any]
    classification_correct: bool | None = None
    verified_name: str | None = None
    verified_kind: CompanyKind | None = None
    verified_domain: str | None = None
    reasoning: str | None = None
    suggested_rules: list[dict[str, Any]] = field(default_factory=list)
    id: UUID | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PromptSnippet:
    """One saved, reusable piece of instruction text (migration 0022) — a
    tone, or anything else worth writing once and reusing wherever a
    free-text "extra instructions" field feeds a model call. Not tied to
    replies specifically, even though that is the only caller today."""

    label: str
    text: str
    id: UUID | None = None
    created_at: datetime | None = None


def is_ghosted(
    *,
    last_message_at: datetime | None,
    last_direction: Direction | None,
    latest_stage: Stage | None,
    now: datetime,
    after_days: int = 21,
) -> bool:
    """Derive whether an application has been ghosted.

    Not a stage, and not stored. Three conditions, all necessary:

    * the last message went **outbound** — silence after their mail is the
      user's turn, not a ghosting;
    * the application has not reached a terminal stage — a rejection followed by
      silence is an ending, and calling it ghosting would inflate the ghost rate
      the PRD wants measured (G5);
    * more than ``after_days`` have passed.

    Because it is derived, changing the threshold re-derives the world for free
    (I3). Storing it would have made this a migration.
    """
    if last_message_at is None or last_direction != "outbound":
        return False
    if latest_stage in TERMINAL_STAGES:
        return False
    return (now - last_message_at).days > after_days
