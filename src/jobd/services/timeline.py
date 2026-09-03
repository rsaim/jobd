"""Reconstructing a company's history (M5 gate 1, PRD G2).

G2's promise is not "a list of events" — it is *"each claim linked to its
evidence message"*. So every line this produces carries the id of the message
that evidences it, and there is no code path that emits a claim without one:
`stage_event.evidence_message_id` is NOT NULL, and the join is inner.

Ghosting appears here and is stored nowhere, which is the M3 decision paying
off — the threshold is an argument, so changing your mind about what counts as
ghosted costs a re-render rather than a migration.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg

from jobd.domain.record import (
    Direction,
    Stage,
    collapse_stage_runs,
    is_ghosted,
    latest_batch,
    resolve_stage_window,
)

#: Stages in the order a hiring process runs through them, used only to break
#: ties when several events share a timestamp. Not an assertion that a real
#: process visits them in this order — plenty skip straight from `applied` to
#: `rejected` — just the sequence a reader expects when the record itself
#: gives no finer ordering.
STAGE_ORDER: tuple[str, ...] = (
    "applied",
    "recruiter_screen",
    "phone_screen",
    "technical",
    "onsite",
    "offer",
    "accepted",
    "declined",
    "withdrawn",
    "rejected",
)


@dataclass(frozen=True, slots=True)
class Claim:
    """One stage transition, and the message that proves it."""

    stage: Stage
    occurred_at: datetime
    evidence_message_id: UUID
    evidence_subject: str | None
    evidence_direction: Direction
    confidence: float | None
    extracted_by: str
    #: The derivation batch this claim came from; None for rows written
    #: before batches were tracked. `latest_batch` reads it, and it is what
    #: lets a re-derivation append rather than destroy (see domain.record).
    derived_at: datetime | None = None
    #: Set by `build` so `latest_batch` can group without a second lookup.
    application_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ApplicationTimeline:
    """One application, with its evidence-linked claims."""

    application_id: UUID
    role_title: str | None
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    message_count: int
    last_message_at: datetime | None
    last_direction: Direction | None
    claims: list[Claim] = field(default_factory=list)

    @property
    def latest_stage(self) -> Stage | None:
        return self.claims[-1].stage if self.claims else None

    def ghosted(self, *, now: datetime, after_days: int = 21) -> bool:
        """Derived, never stored. See jobd.domain.record.is_ghosted."""
        return is_ghosted(
            last_message_at=self.last_message_at,
            last_direction=self.last_direction,
            latest_stage=self.latest_stage,
            now=now,
            after_days=after_days,
        )


@dataclass(frozen=True, slots=True)
class CompanyTimeline:
    """Everything known about one company."""

    company_id: UUID
    canonical_name: str
    domain: str | None
    kind: str
    first_seen_at: datetime
    last_seen_at: datetime
    applications: list[ApplicationTimeline]
    contacts: list[tuple[str | None, str]]
    unlinked_messages: int

    @property
    def spans_years(self) -> int:
        return self.last_seen_at.year - self.first_seen_at.year + 1


def find_company(conn: psycopg.Connection[Any], needle: str) -> UUID | None:
    """Resolve a user-typed name to a company.

    Canonical name, alias, and domain are all tried, because the name a person
    types is whichever one they happen to remember — and the record deliberately
    stores several.
    """
    row = conn.execute(
        "SELECT c.id FROM company c"
        " LEFT JOIN company_alias a ON a.company_id = c.id"
        " WHERE lower(c.canonical_name) = lower(%(n)s)"
        "    OR lower(a.alias) = lower(%(n)s)"
        "    OR lower(c.domain) = lower(%(n)s)"
        "    OR lower(c.canonical_name) LIKE lower(%(like)s)"
        " ORDER BY (lower(c.canonical_name) = lower(%(n)s)) DESC"
        " LIMIT 1",
        {"n": needle, "like": f"%{needle}%"},
    ).fetchone()
    return None if row is None else UUID(str(row[0]))


def build(conn: psycopg.Connection[Any], company_id: UUID) -> CompanyTimeline:
    """Assemble one company's timeline in four queries.

    Four, not one per application: a company with twenty applications would
    otherwise cost twenty round trips to render one page, and M7 renders this
    for every company in an index.
    """
    row = conn.execute(
        "SELECT canonical_name, domain, kind, first_seen_at, last_seen_at"
        " FROM company WHERE id = %s",
        (company_id,),
    ).fetchone()
    if row is None:
        raise KeyError(company_id)

    applications = conn.execute(
        "SELECT a.id, a.role_title, a.started_at, a.ended_at, a.outcome,"
        "       count(m.id) AS message_count,"
        "       max(m.sent_at) AS last_message_at,"
        "       (array_agg(m.direction ORDER BY m.sent_at DESC))[1] AS last_direction"
        " FROM application a"
        " LEFT JOIN message m ON m.application_id = a.id"
        " WHERE a.company_id = %s"
        " GROUP BY a.id ORDER BY a.started_at",
        (company_id,),
    ).fetchall()

    # Ordered by when, then by where the stage sits in a hiring process.
    # `occurred_at` is always its evidence message's `sent_at` (0 of 625 rows
    # differ), so a tie means two messages landed in the same second — four
    # applications have one. With `occurred_at` alone those broke arbitrarily
    # and could render out of process order. The second key is presentation
    # only; it asserts no ordering the record does not already have.
    claims = conn.execute(
        "SELECT s.application_id, s.stage, s.occurred_at, s.evidence_message_id,"
        "       m.subject, m.direction, s.confidence, s.extracted_by,"
        "       s.derived_at"
        " FROM stage_event s"
        # Inner join: structurally, no claim can be emitted without evidence.
        " JOIN message m ON m.id = s.evidence_message_id"
        " JOIN application a ON a.id = s.application_id"
        " WHERE a.company_id = %s"
        " ORDER BY s.occurred_at, array_position(%s::text[], s.stage)",
        (company_id, list(STAGE_ORDER)),
    ).fetchall()

    contacts = conn.execute(
        "SELECT DISTINCT c.display_name, i.identifier FROM contact c"
        " JOIN contact_company cc ON cc.contact_id = c.id"
        " JOIN contact_identity i ON i.contact_id = c.id"
        " WHERE cc.company_id = %s ORDER BY 1, 2",
        (company_id,),
    ).fetchall()

    unlinked = conn.execute(
        "SELECT count(*) FROM message"
        " WHERE company_id = %s AND application_id IS NULL",
        (company_id,),
    ).fetchone()

    by_application: dict[UUID, list[Claim]] = {}
    for claim in claims:
        by_application.setdefault(UUID(str(claim[0])), []).append(
            Claim(
                stage=claim[1],
                occurred_at=claim[2],
                evidence_message_id=UUID(str(claim[3])),
                evidence_subject=claim[4],
                evidence_direction=claim[5],
                confidence=None if claim[6] is None else float(claim[6]),
                extracted_by=claim[7],
                derived_at=claim[8],
                application_id=UUID(str(claim[0])),
            )
        )

    # Stage is extracted per message, but it is a property of the *process*,
    # so every `Re:` in a thread asserts the same event again — 380 of 833
    # rows on the live corpus (45.6%), and `accepted` 67 times across the 13
    # applications that have one. Collapse consecutive same-stage runs to the
    # earliest evidence of each. Derived on read, never stored: the rows stay,
    # so widening or dropping this costs a re-render, not a migration (the
    # same M3 trade `is_ghosted` makes).
    # Two reducers, in order. `collapse_stage_runs` folds repeats of the same
    # stage (one event asserted once per reply in a thread).
    # `resolve_stage_window` then handles the harder half: one interview
    # yields an invite, an update, a reminder and a confirmation, each
    # independently guessed at, so neighbouring stages oscillate within
    # hours. The furthest-along claim in a window wins, and a second collapse
    # folds any same-stage neighbours that merging just created.
    #
    # Live corpus: 833 raw rows -> 377 claims, and stage regressions (a
    # timeline stepping backwards through the funnel) 193 -> 46.
    # Stage events are append-only, so an application that has been derived
    # more than once carries every generation. Show exactly one -- the newest
    # -- before any reducing: rendering the union would put a corrected
    # timeline inside the oscillating one it was written to replace, which is
    # the bug this ordering exists to prevent.
    by_application = {
        app_id: latest_batch(app_claims)
        for app_id, app_claims in by_application.items()
    }
    by_application = {
        app_id: collapse_stage_runs(resolve_stage_window(collapse_stage_runs(app_claims)))
        for app_id, app_claims in by_application.items()
    }

    return CompanyTimeline(
        company_id=company_id,
        canonical_name=row[0],
        domain=row[1],
        kind=row[2],
        first_seen_at=row[3],
        last_seen_at=row[4],
        applications=[
            ApplicationTimeline(
                application_id=UUID(str(a[0])),
                role_title=a[1],
                started_at=a[2],
                ended_at=a[3],
                outcome=terminal_outcome(
                    [c.stage for c in by_application.get(UUID(str(a[0])), [])],
                    a[4],
                ),
                message_count=int(a[5]),
                last_message_at=a[6],
                last_direction=a[7],
                claims=by_application.get(UUID(str(a[0])), []),
            )
            for a in applications
        ],
        contacts=[(c[0], c[1]) for c in contacts],
        unlinked_messages=0 if unlinked is None else int(unlinked[0]),
    )


#: Stages that end an application. Ordered only for readability -- the
#: *last* one claimed wins, not the most severe, because an offer that is
#: later declined is a declined application.
_TERMINAL_STAGES = ("accepted", "rejected", "declined", "withdrawn")


def terminal_outcome(stages: Sequence[str], column: str | None) -> str | None:
    """The application's outcome, preferring what the evidence shows.

    `application.outcome` is a column the per-message pipeline wrote and the
    timeline derivation never updates, so it goes stale the moment stages are
    re-derived: on the live corpus 405 of 461 rows were NULL, and 12 derived
    applications disagreed with their column outright -- one reading
    "withdrawn" where the chain plainly showed a rejection. An application
    whose claims run applied -> onsite -> offer -> accepted was still
    displaying no outcome at all.

    So a derived terminal claim wins. The column survives only where no
    derivation reached, which keeps an untouched application showing exactly
    what it always did.
    """
    stages = list(stages)
    for stage in reversed(stages):
        if stage in _TERMINAL_STAGES:
            return stage
    # Derived, and no terminal stage in it: the application is open, and
    # saying so is the finding. Falling back to the column here kept exactly
    # the rows this function exists to correct -- an application whose chain
    # ends at `onsite` reading "declined", and one ending at `offer` reading
    # "rejected", both straight from stale legacy state.
    return None if stages else column


def render(timeline: CompanyTimeline, *, now: datetime | None = None) -> str:
    """Plain text. M5's deferral is "any UI" — the CLI is the surface."""
    at = now or datetime.now(UTC)
    lines = [
        f"{timeline.canonical_name}"
        + (f"  ({timeline.domain})" if timeline.domain else ""),
        f"{timeline.first_seen_at:%b %Y} - {timeline.last_seen_at:%b %Y}"
        f"   {len(timeline.applications)} application(s)"
        f" across {timeline.spans_years} year(s)",
        "",
    ]

    for application in timeline.applications:
        title = application.role_title or "(role not identified)"
        state = application.outcome or (
            "ghosted" if application.ghosted(now=at) else "open"
        )
        lines.append(
            f"  {application.started_at:%Y-%m-%d}  {title}  [{state}]"
            f"  {application.message_count} message(s)"
        )
        for claim in application.claims:
            confidence = f" {claim.confidence:.2f}" if claim.confidence else ""
            lines.append(
                f"      {claim.occurred_at:%Y-%m-%d}  {claim.stage:<16}"
                f"  ← {claim.evidence_subject or '(no subject)'}"
                f"  [{claim.evidence_message_id}{confidence}]"
            )
        if not application.claims:
            lines.append("      (no evidence-linked stage events)")
        lines.append("")

    if timeline.contacts:
        lines.append("  People")
        for name, address in timeline.contacts:
            lines.append(f"      {name or '(unnamed)'}  <{address}>")
        lines.append("")

    if timeline.unlinked_messages:
        lines.append(
            f"  {timeline.unlinked_messages} message(s) attributed to this company "
            "but not to an application"
        )

    lines.append("  Every claim above cites the message id that evidences it.")
    return "\n".join(lines)
