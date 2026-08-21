"""Reply semantics for an RFC 5322 message — who a reply goes to, what its
subject is, and what its References chain says.

Channel-free on purpose: everything here is derived from the parsed message
itself (`email.message.EmailMessage`, stdlib), the same rules every mail
client implements — Reply-To over From (§3.6.2: Reply-To *is* the sender's
stated answer address, From is just who wrote it), reply-all as
sender + everyone else on the To/Cc lines minus yourself, and a child's
References as the parent's chain plus the parent's own Message-ID (§3.6.4).
Gmail's threadId is deliberately not this module's business — that is the
adapter's channel-specific ref (`ports/sender.py`'s `thread_ref`); these
functions produce only what any RFC-speaking client would.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses


@dataclass(frozen=True, slots=True)
class ReplyRecipients:
    """Resolved recipient lines for a reply, bare addresses only."""

    to: tuple[str, ...]
    cc: tuple[str, ...]


def _addresses(message: EmailMessage, *headers: str) -> list[str]:
    """Bare addresses off one or more headers, in written order."""
    values = [str(v) for h in headers for v in message.get_all(h, [])]
    return [addr for _, addr in getaddresses(values) if addr]


def _dedupe(addresses: list[str], *, drop: set[str]) -> tuple[str, ...]:
    """Order-preserving, case-insensitive dedupe; addresses in `drop`
    (already-lowercased) never appear."""
    seen = set(drop)
    out = []
    for addr in addresses:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            out.append(addr)
    return tuple(out)


def reply_recipients(
    message: EmailMessage, *, self_addresses: set[str], reply_all: bool
) -> ReplyRecipients:
    """Compute Reply / Reply-all recipients the way a mail client does.

    Reply targets Reply-To when present, else From. Replying to your *own*
    message (a follow-up on a cold application) targets the original To
    line instead — answering yourself is never what the click meant.
    Reply-all keeps the same To and moves everyone else on the original
    To/Cc lines into Cc, minus every address in `self_addresses` and
    anything already on the To line.
    """
    self_lower = {a.lower() for a in self_addresses}

    to = _dedupe(
        _addresses(message, "Reply-To") or _addresses(message, "From"), drop=set()
    )
    if not to or all(a.lower() in self_lower for a in to):
        to = _dedupe(_addresses(message, "To"), drop=self_lower)

    cc: tuple[str, ...] = ()
    if reply_all:
        cc = _dedupe(
            _addresses(message, "To", "Cc"),
            drop=self_lower | {a.lower() for a in to},
        )
    return ReplyRecipients(to=to, cc=cc)


def reply_subject(subject: str | None) -> str:
    """`Re: `-prefix a subject exactly once, keeping an existing prefix's
    spelling (``RE:``/``re:``) rather than normalizing what a correspondent
    wrote."""
    text = (subject or "").strip()
    if text.lower().startswith("re:"):
        return text
    return f"Re: {text}".rstrip()


def references_chain(message: EmailMessage) -> str | None:
    """The References line for a reply to `message` (RFC 5322 §3.6.4):
    the parent's own References — or its In-Reply-To when it has none —
    followed by the parent's Message-ID. None when the parent offers no
    ids at all, in which case a reply simply has no chain to carry."""
    parent_chain = str(message.get("References", "")).split() or str(
        message.get("In-Reply-To", "")
    ).split()
    message_id = message.get("Message-ID")
    parts = parent_chain + ([str(message_id)] if message_id else [])
    return " ".join(parts) if parts else None
