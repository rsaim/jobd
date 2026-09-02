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

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
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


#: Anything with a `stage` and an `occurred_at` — `StageEvent` here, and
#: `services.timeline.Claim` on the read side. `collapse_stage_runs` needs
#: only those two attributes, and duplicating it per type would let the two
#: timelines drift apart.
class _HasStage(Protocol):
    @property
    def stage(self) -> Any: ...
    @property
    def occurred_at(self) -> datetime: ...


def collapse_stage_runs[T: _HasStage](events: Sequence[T]) -> list[T]:
    """Fold consecutive same-stage events into the earliest of each run.

    Per-message extraction asks every message in a thread "what stage is
    this?", so one real event becomes as many assertions as the thread has
    replies. Against the live corpus that was 280 of 833 stage events (33.6%)
    describing something already recorded, and ``accepted`` -- an outcome that
    can happen at most once per application -- appearing 67 times across 13
    applications.

    The fold keeps the *earliest* event of each run: the moment a stage is
    first evidenced is when it happened, and the later replies are the same
    conversation continuing. Evidence is preserved either way -- every message
    keeps its `company_id`/`application_id` link (`message_company`), so
    collapsing the timeline never loses the trail back to the mail.

    Deliberately **not** monotonicity enforcement. A later run of an earlier
    stage is kept, because a genuine second recruiter screen with a different
    team is a real event and not a contradiction; only *adjacent* repeats are
    one event. Ordering is by ``occurred_at`` regardless of the caller's input
    order, so a query that returned rows in insertion order still reduces
    correctly.

    Pure and derived, like :func:`is_ghosted` above it: nothing here writes,
    so re-deriving the record (I3) re-derives the collapsed timeline for free.
    """
    if not events:
        return []
    ordered = sorted(events, key=lambda e: e.occurred_at)
    collapsed = [ordered[0]]
    for event in ordered[1:]:
        if event.stage != collapsed[-1].stage:
            collapsed.append(event)
    return collapsed


#: How far a hiring process has actually progressed. Ordering only — a real
#: process skips stages freely, and this asserts nothing about which it must
#: visit. Terminal stages are absent on purpose: they are outcomes, not
#: positions in the funnel, and `resolve_stage_window` never trades one away.
_STAGE_PROGRESS: dict[str, int] = {
    "applied": 0,
    "recruiter_screen": 1,
    "phone_screen": 2,
    "technical": 3,
    "onsite": 4,
    "offer": 5,
}


def resolve_stage_window[T: _HasStage](
    events: Sequence[T], *, within_hours: float = 24.0
) -> list[T]:
    """Within a short window, the furthest-along stage wins.

    `collapse_stage_runs` folds repeats of the *same* stage. This handles the
    other half of the per-message failure: one interview produces an invite,
    an update, a reminder and a confirmation, each independently asked "what
    stage is this?", so the timeline oscillates between neighbouring stages
    within hours. On the live corpus 29% of stage evidence is scheduling
    logistics, and 35% of transitions land inside a single day (p10 = 15
    minutes) — a hiring process does not advance a stage every fifteen
    minutes, so those are one event described four ways.

    The furthest-along claim wins because it is the *specific* one: a
    recruiter writing "technical interview" says something the calendar bot's
    "quick sync" does not, and the generic reading is the one to discard. The
    surviving event keeps the **earliest** timestamp of its window — when the
    event was first evidenced — so merging never drifts a timeline later.

    Terminal stages (`TERMINAL_STAGES`) are passed through untouched: an
    ending is an outcome, not a position in the funnel, and a rejection that
    happens to land beside interview logistics must not be traded for an
    `onsite`. They neither absorb their neighbours nor are absorbed.

    `within_hours` is an argument rather than a constant for the reason
    `is_ghosted`'s threshold is: this is derived on read, so changing your
    mind costs a re-render, not a migration.
    """
    if not events:
        return []
    ordered = sorted(events, key=lambda e: e.occurred_at)
    out: list[T] = []
    window: list[T] = []

    def flush() -> None:
        if window:
            best = max(window, key=lambda e: _STAGE_PROGRESS[str(e.stage)])
            out.append(best if best is window[0] else _restamp(best, window[0]))
            window.clear()

    for event in ordered:
        if str(event.stage) in TERMINAL_STAGES:
            flush()
            out.append(event)
            continue
        if window:
            gap = (event.occurred_at - window[0].occurred_at).total_seconds()
            if gap > within_hours * 3600:
                flush()
        window.append(event)
    flush()
    return sorted(out, key=lambda e: e.occurred_at)


def _restamp[T: _HasStage](winner: T, earliest: T) -> T:
    """The winning stage, dated to when its window opened.

    `dataclasses.replace` keeps the winner's own evidence link — the message
    that made the specific claim stays the proof of it (G2) — while the time
    becomes the moment the event was first seen. Both `StageEvent` and
    `services.timeline.Claim` are frozen dataclasses, so this is total.
    """
    from dataclasses import replace

    return replace(winner, occurred_at=earliest.occurred_at)  # type: ignore[type-var]


def enforce_forward_order[T: _HasStage](events: Sequence[T]) -> list[T]:
    """Keep only events that move the process forward. Drops contradictions.

    Stage ordering is a fixed property of the vocabulary, so enforcing it in
    code is cheaper and more reliable than asking a model to hold the whole
    funnel in mind while reading a chain. The per-application timeline call
    already emits few, well-chosen events; this removes the residue where it
    walked back down the funnel (a "quick sync" after a coding round genuinely
    reads like a screen).

    Demoted events are **dropped, not reordered**. Moving one to where it
    would be monotonic would invent a claim the evidence does not support --
    the message stays linked to the application either way (`message_company`),
    so the evidence trail survives what the timeline declines to assert.

    Terminal stages are the exception in both directions: any of them may
    follow any stage (a process can end from anywhere), several may follow
    each other (`offer` -> `accepted` -> `declined` is a real sequence), and
    nothing non-terminal may follow one -- the process is over, which is the
    same contradiction `classify.py`'s monotonicity guard refuses per message.
    """
    if not events:
        return []
    kept: list[T] = []
    highest = -1
    ended = False
    for event in sorted(events, key=lambda e: e.occurred_at):
        stage = str(event.stage)
        if stage in TERMINAL_STAGES:
            kept.append(event)
            ended = True
            continue
        if ended:
            continue
        rank = _STAGE_PROGRESS[stage]
        if rank <= highest:
            continue
        highest = rank
        kept.append(event)
    return kept
