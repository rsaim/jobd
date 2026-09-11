"""Offline conversations -- the calls that leave no mail.

A search does not happen only in the inbox. A recruiter calls, a hiring
manager talks you through the team over coffee, an offer is made verbally and
you turn it down on the same call. None of that is derivable, because there is
nothing to derive *from*: the mailbox is silent for exactly the interactions
that mattered most.

This module is the human's way in. It is deliberately the only write path in
the codebase that creates a stage event a model did not propose, so the two
rules below are what keep it from corrupting the derived record.

**A conversation is not a message.** `message` holds what a provider handed
us, write-once; a recollection of a phone call has no envelope, so it lives in
`conversation` with the human named as its source (`extracted_by='manual'`).

**A manual stage must join the application's current batch.** Reads keep only
the newest derivation per application (`domain.record.latest_batch`, and
`dashboard._CURRENT_BATCH` in SQL), comparing `stage_event.derived_at` to
`application.derived_at`. A manual row left at `derived_at = NULL` therefore
matches only on applications no derivation has ever reached -- it would be
written successfully, report success, and never appear. So `_batch_of` stamps
it with whatever batch the application is currently showing. Derivation
re-runs delete only their own `extracted_by='timeline'` rows, so the stamp
survives; it means "belongs to the generation on display", not "produced by
that run".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

#: Conversation media. `other` is the escape hatch, not a default.
KINDS = ("phone", "video", "in_person", "other")

#: Stages a human may assert. Mirrors the stage_event CHECK constraint.
STAGES = (
    "applied",
    "recruiter_screen",
    "phone_screen",
    "technical",
    "onsite",
    "offer",
    "rejected",
    "accepted",
    "declined",
    "withdrawn",
)

#: Stages that settle an application: they say how the process *ended*.
#:
#: `offer` is deliberately absent. An offer is a milestone, not an ending --
#: it is followed by an acceptance or a decline, and writing it to `outcome`
#: would erase the ending already recorded. Recording a verbal offer on a
#: chain that ends in a decline must leave that decline standing; the offer
#: reaches the funnel through its own stage event, which is what `had_offer`
#: reads.
_FINAL = {"rejected", "accepted", "declined", "withdrawn"}


@dataclass(frozen=True)
class ConversationRow:
    """One recorded offline conversation, with its company resolved."""

    id: UUID
    company_id: UUID
    company_name: str
    application_id: UUID | None
    occurred_at: datetime
    kind: str
    counterpart: str | None
    notes: str
    stage: str | None


def _batch_of(conn: Any, application_id: UUID) -> datetime | None:
    """The batch stamp a manual row must carry to be visible.

    Returns the application's current `derived_at`. See this module's
    docstring: matching it is what puts a hand-entered stage in the same
    generation the dashboard is reading, rather than in a silently filtered
    one.
    """
    row = conn.execute(
        "SELECT derived_at FROM application WHERE id = %s", (application_id,)
    ).fetchone()
    return row[0] if row else None


def resolve_application(
    conn: Any, company_id: UUID, occurred_at: datetime
) -> UUID | None:
    """Pick which application a conversation belongs to.

    A company can hold several (a re-application, a different team). The one
    whose mail brackets the conversation is the right home; failing that, the
    most recently active. Returns None when the company has no application at
    all, which is legitimate -- a first-contact call can precede any mail.
    """
    row = conn.execute(
        """
        SELECT a.id
        FROM application a
        LEFT JOIN message m ON m.application_id = a.id
        WHERE a.company_id = %s
        GROUP BY a.id
        ORDER BY
            -- An application whose mail spans this moment wins outright.
            (min(m.sent_at) <= %s AND max(m.sent_at) >= %s) DESC NULLS LAST,
            -- Then whichever was live nearest the conversation.
            abs(extract(epoch FROM coalesce(max(m.sent_at), a.started_at) - %s))
                ASC NULLS LAST
        LIMIT 1
        """,
        (company_id, occurred_at, occurred_at, occurred_at),
    ).fetchone()
    return row[0] if row else None


def record(
    conn: Any,
    *,
    company_id: UUID,
    occurred_at: datetime,
    kind: str,
    notes: str = "",
    counterpart: str | None = None,
    stage: str | None = None,
    application_id: UUID | None = None,
) -> UUID:
    """Record one conversation, and the stage it establishes if it has one.

    Returns the conversation id. When `stage` is given, a matching
    `stage_event` is written with `extracted_by='manual'` and stamped into the
    application's current batch. A *final* stage (accepted/declined/rejected/
    withdrawn) also settles `application.outcome`; an `offer` does not, since
    it is a milestone the ending follows -- see `_FINAL`.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    if stage is not None and stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")

    if application_id is None:
        application_id = resolve_application(conn, company_id, occurred_at)

    stage_event_id: UUID | None = None
    if stage is not None and application_id is not None:
        stage_event_id = conn.execute(
            "INSERT INTO stage_event (application_id, stage, occurred_at,"
            " extracted_by, confidence, derived_at)"
            " VALUES (%s, %s, %s, 'manual', 1.0, %s) RETURNING id",
            (application_id, stage, occurred_at, _batch_of(conn, application_id)),
        ).fetchone()[0]
        if stage in _FINAL:
            # Only move the outcome forward in time: a hand-entered decline
            # from last year must not overwrite a more recent acceptance.
            conn.execute(
                "UPDATE application SET outcome = %s,"
                " ended_at = greatest(coalesce(ended_at, %s), %s)"
                " WHERE id = %s AND (ended_at IS NULL OR ended_at <= %s)",
                (stage, occurred_at, occurred_at, application_id, occurred_at),
            )

    return conn.execute(
        "INSERT INTO conversation (company_id, application_id, occurred_at,"
        " kind, counterpart, notes, stage, stage_event_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            company_id,
            application_id,
            occurred_at,
            kind,
            counterpart,
            notes,
            stage,
            stage_event_id,
        ),
    ).fetchone()[0]


def delete(conn: Any, conversation_id: UUID) -> bool:
    """Remove a conversation and the stage event it asserted.

    The stage goes with it: a hand-entered stage has no evidence of its own,
    so leaving it behind would strand a claim in the funnel that nothing in
    the record supports any more.
    """
    row = conn.execute(
        "DELETE FROM conversation WHERE id = %s RETURNING stage_event_id",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return False
    if row[0] is not None:
        conn.execute("DELETE FROM stage_event WHERE id = %s", (row[0],))
    return True


def listing(
    conn: Any,
    *,
    company_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[ConversationRow]:
    """Recorded conversations, most recent first."""
    rows = conn.execute(
        """
        SELECT v.id, v.company_id, c.canonical_name, v.application_id,
               v.occurred_at, v.kind, v.counterpart, v.notes, v.stage
        FROM conversation v
        JOIN company c ON c.id = v.company_id
        WHERE (%(company)s::uuid IS NULL OR v.company_id = %(company)s::uuid)
          AND (%(since)s::timestamptz IS NULL
               OR v.occurred_at >= %(since)s::timestamptz)
          AND (%(until)s::timestamptz IS NULL
               OR v.occurred_at <= %(until)s::timestamptz)
        ORDER BY v.occurred_at DESC
        LIMIT %(limit)s
        """,
        {
            "company": company_id,
            "since": since,
            "until": until,
            "limit": limit,
        },
    ).fetchall()
    return [
        ConversationRow(
            id=r[0],
            company_id=r[1],
            company_name=r[2],
            application_id=r[3],
            occurred_at=r[4],
            kind=r[5],
            counterpart=r[6],
            notes=r[7],
            stage=r[8],
        )
        for r in rows
    ]
