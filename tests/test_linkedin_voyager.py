"""Unit tests for the server-side Voyager puller.

No network: a fake client returns canned Voyager JSON in the LEGACY REST shape
the library yields, and we assert the puller resolves direction, partner,
timestamps and the message window correctly — plus that stale conversations are
skipped without fetching their events.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobd.adapters.linkedin.voyager_source import (
    LinkedInAuthError,
    iter_messages,
    parse_cookie_string,
    self_member_id,
)

SELF = "SELF123"
PARTNER = "PARTNER9"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _member(member_id: str, first: str = "", last: str = "", slug: str = "") -> dict:
    return {
        "com.linkedin.voyager.messaging.MessagingMember": {
            "miniProfile": {
                "entityUrn": f"urn:li:fs_miniProfile:{member_id}",
                "firstName": first,
                "lastName": last,
                "publicIdentifier": slug,
            }
        }
    }


def _message_event(urn: str, sender_id: str, text: str, when: datetime) -> dict:
    return {
        "entityUrn": urn,
        "createdAt": _ms(when),
        "subtype": "MEMBER_TO_MEMBER",
        "from": _member(sender_id),
        "eventContent": {
            "com.linkedin.voyager.messaging.event.MessageEvent": {
                "attributedBody": {"text": text}
            }
        },
    }


class FakeApi:
    def __init__(self, conversations: list[dict], events: dict[str, list[dict]]):
        self._conversations = conversations
        self._events = events
        self.fetched_conversations: list[str] = []

    def get_user_profile(self) -> dict:
        return {"data": {"*miniProfile": f"urn:li:fs_miniProfile:{SELF}"}}

    def get_conversations(self) -> dict:
        return {"elements": self._conversations}

    def get_conversation(self, conversation_id: str) -> dict:
        self.fetched_conversations.append(conversation_id)
        return {"elements": self._events.get(conversation_id, [])}


def _fresh_mailbox(now: datetime) -> FakeApi:
    fresh_urn = "urn:li:fs_conversation:2-fresh=="
    stale_urn = "urn:li:fs_conversation:2-stale=="
    conversations = [
        {
            "entityUrn": fresh_urn,
            # LinkedIn stamps this with the newest message time (the outbound).
            "lastActivityAt": _ms(now + timedelta(minutes=1)),
            "participants": [
                _member(SELF, "Alex", "Doe"),
                _member(PARTNER, "Dana", "Recruiter", "dana-recruiter"),
            ],
        },
        {
            "entityUrn": stale_urn,
            "lastActivityAt": _ms(now - timedelta(days=200)),
            "participants": [_member(SELF), _member("OLD1", "Old", "Contact")],
        },
    ]
    events = {
        "2-fresh==": [
            _message_event("urn:li:fs_event:(2-fresh==,1)", PARTNER, "Hi Alex", now),
            _message_event(
                "urn:li:fs_event:(2-fresh==,2)", SELF, "Thanks!", now + timedelta(minutes=1)
            ),
            # A non-message event (e.g. a participant change) — no MessageEvent.
            {"entityUrn": "urn:li:fs_event:(2-fresh==,3)", "createdAt": _ms(now), "from": _member(PARTNER)},
            # A message older than the window — dropped even in a fresh convo.
            _message_event(
                "urn:li:fs_event:(2-fresh==,0)", PARTNER, "old", now - timedelta(days=100)
            ),
        ],
        "2-stale==": [_message_event("urn:li:fs_event:(2-stale==,9)", "OLD1", "ancient", now)],
    }
    return FakeApi(conversations, events)


def test_self_member_id_from_me():
    api = FakeApi([], {})
    assert self_member_id(api) == SELF


def test_iter_messages_resolves_direction_and_partner():
    now = datetime.now(UTC).replace(microsecond=0)
    api = _fresh_mailbox(now)
    since = now - timedelta(days=90)

    messages = list(iter_messages(api, self_id=SELF, since=since))

    assert [m.text for m in messages] == ["Hi Alex", "Thanks!"]
    assert [m.direction for m in messages] == ["inbound", "outbound"]
    for m in messages:
        assert m.partner_id == PARTNER
        assert m.partner_name == "Dana Recruiter"
        assert m.conversation_id == "urn:li:fs_conversation:2-fresh=="
    assert messages[0].sent_at == now
    assert messages[1].sent_at == now + timedelta(minutes=1)


def test_stale_conversation_events_never_fetched():
    now = datetime.now(UTC).replace(microsecond=0)
    api = _fresh_mailbox(now)
    since = now - timedelta(days=90)

    list(iter_messages(api, self_id=SELF, since=since))

    # The 200-day-stale conversation is skipped before its events are fetched.
    assert api.fetched_conversations == ["2-fresh=="]


def test_window_drops_messages_at_or_before_since():
    now = datetime.now(UTC).replace(microsecond=0)
    api = _fresh_mailbox(now)
    since = now  # strictly-after: the now-stamped inbound is excluded

    messages = list(iter_messages(api, self_id=SELF, since=since))

    assert [m.text for m in messages] == ["Thanks!"]


def test_parse_cookie_string_keeps_quotes_and_requires_both():
    jar = parse_cookie_string('li_at=AQ123; JSESSIONID="ajax:99"; lang=en')
    assert jar["li_at"] == "AQ123"
    assert jar["JSESSIONID"] == '"ajax:99"'  # quotes kept — CSRF value must match

    with pytest.raises(LinkedInAuthError):
        parse_cookie_string("li_at=only")
