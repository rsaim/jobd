"""Sender — outbound, human-gated (PRD P5, invariants I1 and I5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Draft:
    """An unsent message. The only thing v1 produces.

    ``in_reply_to`` and ``references`` are the RFC 5322 threading headers,
    already resolved by the caller (`domain/reply.py`) — channel-agnostic,
    understood by every mail client. ``thread_ref`` is the one concession to
    channels that keep their *own* conversation id (Gmail's ``threadId``):
    an opaque string the caller read off the message being replied to and
    the adapter passes back, never interpreted by anyone in between. It
    exists because RFC headers thread a message only once it is *sent* —
    a channel's UI groups an unsent draft into its conversation by the
    channel's own id or not at all.
    """

    channel: str
    account: str
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = ()
    in_reply_to: str | None = None
    references: str | None = None
    thread_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    """A recorded human decision. Required for every send, with no exceptions.

    Passing this object is the *only* way to reach :meth:`Sender.send`, so I1 is
    a type-level property rather than a runtime check somebody can skip. There
    is no flag that constructs one automatically — an ``Approval`` exists only
    because a person made one.
    """

    draft_id: str
    approved_by: str
    approved_at_iso: str


@runtime_checkable
class Sender(Protocol):
    """Writes drafts, and sends only what a human approved.

    This port is never handed to the planning model. The planner holds
    read-and-draft tools only (I5), so no instruction hidden inside an ingested
    email can reach :meth:`send` — there is no tool to call. See SECURITY.md §4.

    The executor that calls :meth:`send` is v1.5 (P5). v1 stops at
    :meth:`draft`.
    """

    @property
    def channel(self) -> str:
        """Channel this sender writes to, e.g. ``"gmail"``."""
        ...

    def draft(self, draft: Draft) -> str:
        """Write to the channel's drafts folder. Returns a draft id. Sends nothing."""
        ...

    def send(self, account: str, draft_id: str, approval: Approval) -> str:
        """Send a previously approved draft. Returns the sent-message id.

        ``account`` matches the account the draft was created under
        (``Draft.account``) — a multi-account channel like Gmail routes
        through a different credential per account, and the executor is the
        one that knows which account this draft belongs to (it is the one
        that called :meth:`draft`), not this method.

        Implementations must verify ``approval.draft_id == draft_id`` and refuse
        otherwise. Rate limiting, idempotency, and the audit log belong to the
        executor around this call, not inside it.
        """
        ...
