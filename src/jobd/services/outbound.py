"""Draft and send a reply (PRD P5, I1, ports/sender.py).

Two functions, matching `Sender`'s two methods, and a durable row in
`outbound_message` written at each step — that row *is* the "recorded human
approval" I1 requires, not a flag saying one happened (see migration 0019's
own comment on why it has no foreign key back into `message`).

Never called from `services/chat.py`'s tool loop — the chat's connection is
read-only by construction (`SET TRANSACTION READ ONLY`, issued before the
registry that would need to call this even exists) and this needs a normal
read-write one. Called only from a route a human's own click reaches
(`web/api.py`), same split `docs/chat-and-summaries.md` §2 draws for summary
generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from typing import Any
from uuid import UUID

import psycopg

from jobd.domain import reply
from jobd.ports import Storage
from jobd.ports.sender import Approval, Draft, Sender


class OutboundError(ValueError):
    """A request that is wrong on its own terms — no message to reply to,
    already sent, id mismatch. Never a transport failure; those propagate
    from `Sender` unchanged so the caller sees the real cause (e.g.
    `SendNotAuthorized`)."""


@dataclass(frozen=True, slots=True)
class OutboundRow:
    id: UUID
    status: str
    draft_id: str
    subject: str
    body: str
    recipients: tuple[str, ...]
    account: str
    sent_message_id: str | None
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


def _row(record: tuple[Any, ...]) -> OutboundRow:
    return OutboundRow(
        id=record[0],
        status=record[1],
        draft_id=record[2],
        subject=record[3],
        body=record[4],
        recipients=tuple(record[5]),
        account=record[6],
        sent_message_id=record[7],
        approved_by=record[8],
        approved_at=record[9],
        created_at=record[10],
    )


_COLS = (
    "id, status, draft_id, subject, body, recipients, account,"
    " sent_message_id, approved_by, approved_at, created_at"
)


def create_draft(
    conn: psycopg.Connection[Any],
    sender: Sender,
    storage: Storage,
    *,
    account: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    in_reply_to_message_id: UUID | None = None,
) -> OutboundRow:
    """Write a draft — to the channel's own drafts folder via `Sender.draft`,
    and to `outbound_message` so it exists in the record even if nobody ever
    sends it.

    `in_reply_to_message_id` is *our* message id, not a channel header — this
    function is what resolves it into the actual `In-Reply-To`/`References`
    values by re-fetching that message's raw bytes (I3: re-deriving headers
    from storage, not new columns), and into the channel's own conversation
    ref (`thread_id`, already a `message` column) so the draft lands *inside*
    the conversation rather than beside it. None of that resolution is the
    `Sender` port's concern; a `Draft` carries only already-resolved strings.
    """
    cc = cc or []
    if not to:
        raise OutboundError("No recipient.")
    # A real gap, not a hypothetical: a model given only a contact's display
    # name (no address in the tool result it read) drafted `to="Natalia
    # Knop"` — found live, testing propose_draft_reply. message_detail now
    # exposes the real address (registry.py), but this is the backstop for
    # whatever still gets past that: an obviously-not-an-address string
    # should never reach Sender.draft, whatever produced it.
    bad = [addr for addr in to + cc if "@" not in addr]
    if bad:
        raise OutboundError(f"Not an email address: {', '.join(bad)!r}.")

    in_reply_to_header = None
    references = None
    thread_ref = None
    if in_reply_to_message_id is not None:
        row = conn.execute(
            "SELECT storage_key, thread_id FROM message WHERE id = %(id)s",
            {"id": in_reply_to_message_id},
        ).fetchone()
        if row is not None:
            thread_ref = row[1]
            raw = storage.get(row[0])
            parsed = message_from_bytes(raw.payload, policy=policy.default)
            in_reply_to_header = parsed.get("Message-ID")
            references = reply.references_chain(parsed)

    draft = Draft(
        channel=sender.channel,
        account=account,
        to=tuple(to),
        cc=tuple(cc),
        subject=subject,
        body=body,
        in_reply_to=str(in_reply_to_header) if in_reply_to_header else None,
        references=references,
        thread_ref=thread_ref,
    )
    draft_id = sender.draft(draft)

    record = conn.execute(
        f"""
        INSERT INTO outbound_message
            (channel, account, in_reply_to_message_id, recipients, subject, body, draft_id)
        VALUES (%(channel)s, %(account)s, %(reply_to)s, %(recipients)s, %(subject)s, %(body)s, %(draft_id)s)
        RETURNING {_COLS}
        """,
        {
            "channel": sender.channel,
            "account": account,
            "reply_to": in_reply_to_message_id,
            # One audited list of everyone this can reach — To and Cc are a
            # header-placement detail the record does not re-litigate.
            "recipients": list(to) + list(cc),
            "subject": subject,
            "body": body,
            "draft_id": draft_id,
        },
    ).fetchone()
    conn.commit()
    assert record is not None
    return _row(record)


def approve_and_send(
    conn: psycopg.Connection[Any],
    sender: Sender,
    *,
    outbound_id: UUID,
    approved_by: str,
) -> OutboundRow:
    """The only path that ever calls `Sender.send` — a human's click on this
    exact endpoint *is* the approval; `Approval` is constructed here, from
    that click, never earlier."""
    existing = conn.execute(
        f"SELECT {_COLS} FROM outbound_message WHERE id = %(id)s",
        {"id": outbound_id},
    ).fetchone()
    if existing is None:
        raise OutboundError("No such draft.")
    row = _row(existing)
    if row.status == "sent":
        raise OutboundError("Already sent — refusing to send twice.")

    approved_at = datetime.now(UTC)
    approval = Approval(
        draft_id=row.draft_id,
        approved_by=approved_by,
        approved_at_iso=approved_at.isoformat(),
    )

    # Atomically claim the draft for sending: UPDATE only succeeds if status is
    # still 'draft'. If another process sent it between our check and this
    # statement, rowcount will be 0 and we bail before the send call (the race
    # condition this fixes — two processes checking "sent?" then both calling
    # send() because neither has updated the row yet).
    claim = conn.execute(
        """
        UPDATE outbound_message
        SET status = 'sending', approved_by = %(by)s, approved_at = %(at)s
        WHERE id = %(id)s AND status = 'draft'
        RETURNING draft_id
        """,
        {"by": approved_by, "at": approved_at, "id": outbound_id},
    )
    if claim.rowcount == 0:
        # Already sent (or never existed). Re-read to raise the right error.
        fresh = conn.execute(
            f"SELECT {_COLS} FROM outbound_message WHERE id = %(id)s",
            {"id": outbound_id},
        ).fetchone()
        if fresh and _row(fresh).status == "sent":
            raise OutboundError("Already sent — refusing to send twice.")
        raise OutboundError(f"Outbound {outbound_id} not found or not a draft.")

    # Now send the actual message. If this fails the row stays at status='sending',
    # which is fine — that's evidence of the partial state and a manual cleanup path.
    sent_message_id = sender.send(row.account, row.draft_id, approval)

    record = conn.execute(
        f"""
        UPDATE outbound_message
        SET status = 'sent', sent_message_id = %(sent_id)s
        WHERE id = %(id)s
        RETURNING {_COLS}
        """,
        {"sent_id": sent_message_id, "id": outbound_id},
    ).fetchone()
    conn.commit()
    assert record is not None
    return _row(record)
