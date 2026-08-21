"""Reading the *envelope* of a raw message — headers only, no body.

Two halves, arriving in two milestones.

:func:`parse` is M4's: header-level facts only — when it was sent, its subject,
which way it went. The minimum a `message` row cannot exist without.

:func:`body_text` is M5's, called during the classification pass from the same
raw bytes with no re-fetch. It stayed unwritten through M4 on purpose:
full-text search over a body nobody had extracted would have returned nothing
and looked broken, where an empty column is obviously unpopulated.

Neither calls an LLM or resolves an entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape

from jobd.domain.prefilter import domain_of
from jobd.domain.record import Direction


@dataclass(frozen=True, slots=True)
class Envelope:
    """Header-level facts about one message."""

    sent_at: datetime
    direction: Direction
    subject: str | None
    message_id: str | None
    #: The From address, lowercased. None when the header is missing or
    #: unparseable — never guessed at.
    sender_address: str | None = None
    #: `domain_of(sender_address)`, or None. Split out rather than derived at
    #: query time so `message_sender_domain_idx` (migration 0007) can answer
    #: "every message from this company" in SQL, not app code (P4.1's rule).
    sender_domain: str | None = None
    #: Every To/Cc address, lowercased, the user's own account excluded — the
    #: other half of "involving the same people" (see migration 0007): who an
    #: *outbound* reply went to, not just who sent something inbound.
    recipient_addresses: tuple[str, ...] = ()


def parse(payload: bytes, account: str, *, fallback: datetime) -> Envelope:
    """Extract envelope facts from RFC 5322 bytes.

    Args:
        payload: The original message, exactly as stored.
        account: The mailbox this arrived in. Decides direction.
        fallback: Used when the Date header is missing or unparseable, which
            real mail manages more often than it should. Callers pass
            ``fetched_at`` — wrong, but ordered, and a row that exists with an
            approximate date beats a message silently dropped.

    Never raises on malformed input. A five-year archive contains mail from
    every broken client ever written, and one of them must not stop an import.
    """
    parsed = message_from_bytes(payload, policy=policy.default)
    sender = _sender_address(parsed.get_all("From"))
    recipients = _recipient_addresses(
        parsed.get_all("To"), parsed.get_all("Cc"), account
    )

    return Envelope(
        sent_at=_sent_at(parsed.get("Date"), fallback),
        direction=_direction(parsed.get_all("From"), account),
        subject=_header(parsed.get("Subject")),
        message_id=_header(parsed.get("Message-ID")),
        sender_address=sender,
        sender_domain=domain_of(sender) or None if sender else None,
        recipient_addresses=recipients,
    )


def body_text(payload: bytes, *, limit: int = 20_000) -> str:
    """Extract readable text from a message. M5's half of the parse.

    Prefers `text/plain`. Falls back to stripping tags out of `text/html`,
    because a great deal of recruiting mail is HTML-only and dropping it would
    lose the exact corpus this project exists to read.

    Args:
        limit: Truncation point. The signals worth extracting are near the top,
            and quoted reply chains below can run to hundreds of kilobytes —
            paying to embed and classify the same thread twenty times over.

    Never raises. A message that cannot be parsed yields an empty string, which
    the caller records rather than treating as a failure.
    """
    try:
        parsed = message_from_bytes(payload, policy=policy.default)
    except Exception:
        return ""

    chosen = ""
    try:
        part = parsed.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            chosen = content if isinstance(content, str) else ""
            if (part.get_content_subtype() or "") == "html":
                chosen = _strip_html(chosen)
    except Exception:
        chosen = ""

    return _strip_quoted(_collapse(chosen))[:limit]


#: Where a reply's quoted-thread history starts, in the two conventions real
#: mail actually uses (Gmail/Apple Mail's "On ... wrote:" line, and Outlook's
#: plain-text "From:/Sent:/To:/Subject:" header block — often with no
#: "-----Original Message-----" separator at all, just straight into the
#: block, so that separator can't be relied on alone and isn't matched here
#: on its own weight; it only ever appears alongside one of the two below).
#: Also where a known boilerplate footer starts — a different kind of
#: trailing noise than a quoted thread, but the same operation (cut here,
#: keep what's above), so it lives in the same list rather than a second
#: scan over the same text.
_QUOTE_MARKERS = (
    re.compile(r"^On .{0,300}?wrote:[ \t]*$", re.M | re.S),
    re.compile(r"^From:.*\n[ \t]*Sent:.*\n[ \t]*To:.*\n[ \t]*Subject:.*$", re.M),
    # LinkedIn's "this email was intended for <name> (<headline>)" identity
    # blurb — real in 4,502 stored messages, always preceded by a bare
    # dashed divider line that this cuts too, so no orphaned "----" survives.
    re.compile(r"-{3,}\s*\n+\s*This email was intended for.*", re.S),
    re.compile(r"This email was intended for.*", re.S),
)


def _strip_quoted(text: str) -> str:
    """Cut a body at its first quoted-reply marker or known footer — what
    this message's author actually wrote this time, not the thread beneath
    it or the boilerplate below that.

    Every mail client re-embeds the prior thread under a reply, so message N
    of an N-message thread stores N copies of message 1's text uncut. That is
    the entire reason a thread's messages, read as a list, look like
    redundant duplicates of each other — each one already contains all the
    others. Classification mostly doesn't need the cut copy anyway: a reply
    in an already-linked thread is resolved by `classify.py`'s free
    thread-carry, never re-extracted from its (quoted-heavy) body.
    """
    earliest = len(text)
    for pattern in _QUOTE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()
    return text[:earliest].rstrip()


def _strip_html(html: str) -> str:
    """Crude but predictable: drop script/style, drop tags, unescape."""
    without_blocks = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    return unescape(re.sub(r"<[^>]+>", " ", without_blocks))


def _collapse(text: str) -> str:
    """Normalise whitespace so the same message hashes and embeds the same.

    Also drops NUL bytes: Postgres `text` columns reject them outright
    (`DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`),
    and malformed/binary-misdecoded mail bodies do occasionally carry one.
    Stripping here means the one function every extracted body passes
    through makes the value safe to write, rather than every write site
    needing to know about a Postgres quirk.
    """
    collapsed = re.sub(r"\r\n?", "\n", text).replace("\xa0", " ").replace("\x00", "")
    return re.sub(r"[ \t]+", " ", collapsed).strip()


def _sent_at(raw: object, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        parsed = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return fallback
    if parsed is None:
        return fallback
    # Naive dates exist in the wild. Treating them as UTC keeps the column
    # timestamptz and the ordering stable; guessing a local zone would not.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _direction(from_headers: list[object] | None, account: str) -> Direction:
    """Outbound when the user sent it.

    Compared against the mailbox address, not against a configured identity
    list: at M4 the only thing known for certain is which account the bytes came
    from. Aliases and send-as addresses are an M5 entity-resolution problem, and
    guessing here would put wrong directions in the record that M5 then has to
    unpick.
    """
    if not from_headers:
        return "inbound"
    senders = {addr.lower() for _, addr in getaddresses([str(h) for h in from_headers])}
    return "outbound" if account.lower() in senders else "inbound"


def _sender_address(from_headers: list[object] | None) -> str | None:
    """The first From address, lowercased. None on a missing/malformed header."""
    if not from_headers:
        return None
    addrs = getaddresses([str(h) for h in from_headers])
    for _, address in addrs:
        if address:
            return address.lower()
    return None


def _recipient_addresses(
    to_headers: list[object] | None, cc_headers: list[object] | None, account: str
) -> tuple[str, ...]:
    """Every distinct To/Cc address, lowercased, minus the user's own account.

    The account is excluded for the same reason `_known_contact` excludes it
    in classify.py: it appears on every outbound message, so keeping it would
    make "involving the same people" true of the entire mailbox.
    """
    headers = [str(h) for h in (to_headers or []) + (cc_headers or [])]
    seen: dict[str, None] = {}
    for _, address in getaddresses(headers):
        lowered = address.lower()
        if lowered and lowered != account.lower():
            seen[lowered] = None
    return tuple(seen)


def _header(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
