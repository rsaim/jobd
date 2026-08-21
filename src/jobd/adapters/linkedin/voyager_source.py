"""Server-side LinkedIn inbox puller.

The automated-sync counterpart to the companion extension and the console
collector. Where those run Voyager inside the user's own browser, this pulls
the same inbox from the server with the ``linkedin-api`` library
(github.com/tomquirk/linkedin-api), authenticated by a copied session cookie.
No browser stays in the loop, so a cron'd ``just linkedin-sync`` keeps jobd
current on its own.

The catch, stated where the code lives so nobody rediscovers it: this runs from
wherever the server runs. In a codespace that is a datacenter IP, and Voyager
traffic from a datacenter IP replaying a copied cookie is exactly the pattern
LinkedIn restricts accounts for. It is mitigated here — a real ``li_at``
cookie (no scriptable login to challenge), the library's built-in randomized
delay between calls, one poll on a schedule rather than a tight loop — but not
removed. The browser paths carry no such risk; this trades that safety for
unattended sync, a trade the operator opted into.

Everything downstream is unchanged: each message becomes the same deterministic
RFC 5322 (:func:`to_raw_message`) the push endpoint produces, so the archive
keys, threading and classifier do not learn a second shape, and re-pulling an
overlapping window is idempotent (I2).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from jobd.adapters.linkedin.push import PushedMessage

# The two Voyager containers whose keys we read out of the library's raw JSON.
_MEMBER = "com.linkedin.voyager.messaging.MessagingMember"
_MESSAGE_EVENT = "com.linkedin.voyager.messaging.event.MessageEvent"


class LinkedInAuthError(RuntimeError):
    """The session cookie is missing, malformed, or rejected by LinkedIn."""


def parse_cookie_string(raw: str) -> Any:
    """Build a ``RequestsCookieJar`` from a pasted cookie string.

    Accepts what the browser hands you: the value of ``document.cookie`` in a
    linkedin.com tab, or the ``cookie:`` request header of any Voyager call in
    the Network panel — a ``name=value; name=value`` list. Only the two
    cookies the library needs are required; the rest are kept if present but do
    no harm.

    ``li_at`` is the session. ``JSESSIONID`` doubles as the CSRF token (the
    library sends it as the ``Csrf-Token`` header, so its stored value —
    quotes and all — has to match the cookie the server sees).
    """
    from requests.cookies import RequestsCookieJar  # lazy: optional dependency

    jar = RequestsCookieJar()
    for part in raw.strip().split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        jar.set(name.strip(), value.strip(), domain=".linkedin.com", path="/")

    missing = [c for c in ("li_at", "JSESSIONID") if c not in jar]
    if missing:
        raise LinkedInAuthError(
            f"cookie string is missing {', '.join(missing)} — copy the full "
            "document.cookie from a logged-in linkedin.com tab"
        )
    return jar


def client_from_cookies(jar: Any, *, proxies: dict[str, str] | None = None) -> Any:
    """Authenticate the Voyager client with a cookie jar, never a password.

    Password login from a datacenter IP triggers a checkpoint almost every
    time; a valid ``li_at`` skips the login flow entirely. Raises
    :class:`LinkedInAuthError` if the library cannot import or the cookies are
    rejected on the first call.
    """
    try:
        from linkedin_api import Linkedin
    except ImportError as exc:  # optional extra
        raise LinkedInAuthError(
            "linkedin-api is not installed — `pip install -e '.[linkedin]'`"
        ) from exc

    try:
        return Linkedin("", "", cookies=jar, proxies=proxies or {})
    except Exception as exc:  # noqa: BLE001 — library raises bare exceptions
        raise LinkedInAuthError(f"cookie authentication failed: {exc}") from exc


def _member_id(urn: str | None) -> str | None:
    """Trailing id of a ``urn:li:fs_miniProfile:<id>`` (or any URN)."""
    if not urn or ":" not in urn:
        return None
    return urn.rsplit(":", 1)[-1]


def self_member_id(api: Any) -> str:
    """The logged-in member's stable id — the account key jobd stores under.

    The member id never changes; a vanity slug can, and a drifting account
    string would re-hash the whole archive. Dug out of ``/me`` defensively
    because its normalized shape has moved between LinkedIn revisions.
    """
    me = api.get_user_profile()
    urn = (me.get("data", {}) or {}).get("*miniProfile") or me.get("*miniProfile")
    member_id = _member_id(urn)
    if not member_id:
        for item in me.get("included", []) or []:
            if "MiniProfile" in (item.get("$type") or ""):
                member_id = _member_id(item.get("entityUrn"))
                if member_id:
                    break
    if not member_id and me.get("plainId"):
        member_id = str(me["plainId"])
    if not member_id:
        raise LinkedInAuthError("could not resolve the logged-in member id from /me")
    return member_id


def _mini(container: dict[str, Any]) -> dict[str, Any]:
    """The ``miniProfile`` inside a MessagingMember wrapper, or ``{}``."""
    member = container.get(_MEMBER) or container
    return member.get("miniProfile") or {}


def _participants(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    return [_mini(p) for p in conversation.get("participants", []) if _mini(p)]


def _message_text(event: dict[str, Any]) -> str:
    content = event.get("eventContent") or {}
    message = content.get(_MESSAGE_EVENT)
    if not isinstance(message, dict):
        return ""
    body = message.get("attributedBody") or {}
    return body.get("text") or message.get("body") or ""


def _event_to_message(
    event: dict[str, Any],
    *,
    conversation_urn: str,
    partner_id: str,
    partner_name: str,
    self_id: str,
) -> PushedMessage | None:
    """One Voyager event → :class:`PushedMessage`, or ``None`` if unusable.

    Dropped rather than guessed at (same rule as the browser paths): a message
    with no stable URN, no timestamp, or an empty body cannot be represented
    without inventing a field, and an invented field would duplicate the
    archive on the next pull.
    """
    text = _message_text(event)
    external_id = event.get("entityUrn") or event.get("dashEntityUrn")
    created_at = event.get("createdAt")
    if not external_id or not created_at or not str(text).strip():
        return None

    sender_id = _member_id(_mini(event.get("from") or {}).get("entityUrn"))
    return PushedMessage(
        external_id=external_id,
        conversation_id=conversation_urn,
        direction="outbound" if sender_id and sender_id == self_id else "inbound",
        partner_id=partner_id,
        partner_name=partner_name,
        text=str(text),
        sent_at=datetime.fromtimestamp(created_at / 1000, tz=UTC),
    )


def iter_messages(api: Any, *, self_id: str, since: datetime) -> Iterator[PushedMessage]:
    """Yield every inbox message newer than ``since``, oldest bound first.

    Walks the conversation list, then each conversation's events. The
    library's ``default_evade`` sleeps a random fraction of a second before
    each request, so the pull is already paced without an explicit throttle.
    Conversations whose last activity predates ``since`` are skipped without
    fetching their events.
    """
    since_ms = int(since.timestamp() * 1000)
    conversations = (api.get_conversations() or {}).get("elements", []) or []

    for conversation in conversations:
        conversation_urn = conversation.get("entityUrn")
        if not conversation_urn:
            continue
        if (conversation.get("lastActivityAt") or 0) <= since_ms:
            continue

        participants = _participants(conversation)
        others = [p for p in participants if _member_id(p.get("entityUrn")) != self_id]
        partner = others[0] if others else (participants[0] if participants else {})
        partner_id = _member_id(partner.get("entityUrn")) or partner.get(
            "publicIdentifier"
        ) or "unknown"
        partner_name = (
            " ".join(
                x for x in (partner.get("firstName"), partner.get("lastName")) if x
            )
            or partner.get("publicIdentifier")
            or ""
        )

        conversation_id = conversation_urn.rsplit(":", 1)[-1]
        events = (api.get_conversation(conversation_id) or {}).get("elements", []) or []
        for event in events:
            if (event.get("createdAt") or 0) <= since_ms:
                continue
            message = _event_to_message(
                event,
                conversation_urn=conversation_urn,
                partner_id=partner_id,
                partner_name=partner_name,
                self_id=self_id,
            )
            if message is not None:
                yield message


__all__ = [
    "LinkedInAuthError",
    "client_from_cookies",
    "iter_messages",
    "parse_cookie_string",
    "self_member_id",
]
