"""LinkedIn DMs, pushed by the companion extension, rendered as `RawMessage`.

LinkedIn offers no server-side message API to third parties, so the bytes
cannot come from a fetch the way Gmail's do. Instead a companion browser
extension (distributed separately) reads the user's own inbox
via the Voyager API inside their logged-in browser tab and POSTs batches to
``/api/linkedin/push``. This module turns one pushed message into the
archive's canonical form.

The payload is a synthetic RFC 5322 rendering rather than the Voyager JSON.
That choice buys the whole existing pipeline for free — ``envelope.parse``,
``body_text``, classification — at the cost of the payload being a rendering
of what the extension sent, not LinkedIn's own bytes. The rendering is the
source of truth here because it is the only stable form: Voyager responses
carry volatile fields (read state, reactions) that would change the content
hash on every sync and defeat idempotent re-push (I2).

Determinism is therefore the invariant this module exists to hold: the same
logical message must render to byte-identical payloads on every push, because
the storage key is a content hash and storage is write-once. Nothing here may
read a clock, and every header is derived from stable Voyager identifiers.

Participants get synthetic addresses under ``linkedin.invalid`` — RFC 2606
reserves the TLD, so a rendered message can never be mistaken for routable
mail, and ``envelope._direction``'s address comparison works unchanged when
the account is given the same synthetic form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from types import MappingProxyType

from jobd.domain.keys import OCCURRED_AT_META_KEY
from jobd.domain.raw import RawMessage

SOURCE = "linkedin"

#: Synthetic-address domain. Reserved by RFC 2606: never routable, never real.
ADDRESS_DOMAIN = "linkedin.invalid"

_LOCAL_UNSAFE = re.compile(r"[^a-z0-9._-]+")


class PushError(ValueError):
    """Raised when a pushed message cannot be rendered deterministically."""


@dataclass(frozen=True, slots=True)
class PushedMessage:
    """One message as the extension reports it.

    Attributes:
        external_id: Voyager message URN — the source's own stable id.
        conversation_id: Voyager conversation URN. Becomes the thread id.
        direction: ``"inbound"`` or ``"outbound"``, resolved by the extension
            against the logged-in member's own URN (the only party it can
            identify with certainty).
        partner_id: Conversation partner's public slug (``/in/<slug>``) or
            URN suffix when the profile is anonymized.
        partner_name: Partner's display name, as Voyager reports it.
        text: Full message text. The extension must not truncate — this body
            is what classification reads.
        sent_at: LinkedIn's ``deliveredAt`` for the message. The extension
            drops messages without one rather than substituting its clock,
            because a fabricated timestamp would change the payload between
            two pushes of the same message.
    """

    external_id: str
    conversation_id: str
    direction: str
    partner_id: str
    partner_name: str
    text: str
    sent_at: datetime


def synthetic_address(identifier: str) -> str:
    """Map a LinkedIn identifier to a deterministic ``linkedin.invalid`` address.

    Lowercased and restricted to atext-safe characters so the result parses as
    an addr-spec everywhere. Distinct slugs collapsing to one local part (two
    slugs differing only in an emoji, say) is theoretically possible and
    accepted: addresses are identity hints, and the message URN — not the
    address — is what keys provenance.
    """
    local = _LOCAL_UNSAFE.sub("-", identifier.strip().lower()).strip("-.")
    if not local:
        raise PushError(f"identifier {identifier!r} has no addressable characters")
    return f"{local}@{ADDRESS_DOMAIN}"


def account_address(account_slug: str) -> str:
    """The account string ingest rows carry, in the same synthetic form."""
    return synthetic_address(account_slug)


def _display(name: str) -> str:
    """Quote a display name for a From/To header. UTF-8 kept raw — this mail
    is never sent, and an encoded-words pass would add a second spelling of
    the same name for the hash to diverge on."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def to_raw_message(
    message: PushedMessage,
    *,
    account_slug: str,
    fetched_at: datetime | None = None,
) -> RawMessage:
    """Render one pushed message into the archive envelope.

    Args:
        message: The extension's report, already validated at the API edge.
        account_slug: The logged-in member's public slug — which mailbox this
            is. Becomes ``RawMessage.account`` in synthetic-address form so
            direction falls out of the ordinary From comparison.
        fetched_at: Injected in tests. Provenance only; never hashed.

    Raises:
        PushError: On a direction that is neither inbound nor outbound, or an
            identifier that cannot form an address. Raised rather than guessed:
            a wrong direction is a silently-flipped conversation.
    """
    account = account_address(account_slug)
    partner = synthetic_address(message.partner_id)
    partner_header = f"{_display(message.partner_name)} <{partner}>" if message.partner_name else f"<{partner}>"

    if message.direction == "outbound":
        sender, recipient = f"<{account}>", partner_header
    elif message.direction == "inbound":
        sender, recipient = partner_header, f"<{account}>"
    else:
        raise PushError(f"direction {message.direction!r} is not inbound/outbound")

    sent_utc = message.sent_at.astimezone(UTC)
    message_id = synthetic_address(message.external_id)

    # Hand-assembled rather than email.message: the generator's folding rules
    # are an implementation detail of the stdlib version, and a Python upgrade
    # must not re-key the archive.
    headers = (
        f"From: {sender}\r\n"
        f"To: {recipient}\r\n"
        f"Date: {format_datetime(sent_utc)}\r\n"
        f"Subject: LinkedIn message\r\n"
        f"Message-ID: <{message_id}>\r\n"
        f"X-LinkedIn-Conversation: {message.conversation_id}\r\n"
        f"X-LinkedIn-Message-Urn: {message.external_id}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
    )
    payload = headers.encode("utf-8") + message.text.encode("utf-8")

    return RawMessage(
        source=SOURCE,
        external_id=message.external_id,
        account=account,
        fetched_at=fetched_at or datetime.now(UTC),
        payload=payload,
        metadata=MappingProxyType(
            {
                OCCURRED_AT_META_KEY: sent_utc.isoformat(),
                "thread_id": message.conversation_id,
                "partner_name": message.partner_name,
                "partner_id": message.partner_id,
            }
        ),
    )
