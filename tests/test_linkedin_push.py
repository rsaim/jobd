"""LinkedIn push adapter: determinism, direction, and ingest reuse.

The property under test is I2 in push form: the same logical message must
render to byte-identical payloads on every push, because the storage key is a
content hash and a drifting render would duplicate the archive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone, timedelta

import pytest

from jobd.adapters.linkedin.push import (
    PushError,
    PushedMessage,
    account_address,
    synthetic_address,
    to_raw_message,
)
from jobd.domain import envelope
from jobd.domain.keys import storage_key
from jobd.domain.raw import RawMessage
from jobd.services.ingest import ingest_raw

ACCOUNT_SLUG = "doe-alex"


def _pushed(**overrides) -> PushedMessage:
    base = dict(
        external_id="urn:li:msg_message:2-MTc1NTQ0",
        conversation_id="urn:li:msg_conversation:(urn:li:fsd_profile:ACo,2-YZ)",
        direction="inbound",
        partner_id="jane-recruiter",
        partner_name="Jane Recruiter",
        text="Hi Alex — are you open to a Staff role at Acme?",
        sent_at=datetime(2026, 8, 15, 14, 30, 12, tzinfo=UTC),
    )
    base.update(overrides)
    return PushedMessage(**base)


class TestDeterminism:
    def test_same_input_renders_identical_bytes(self):
        a = to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)
        b = to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)
        assert a.payload == b.payload
        assert storage_key(a) == storage_key(b)

    def test_fetched_at_does_not_affect_key(self):
        a = to_raw_message(
            _pushed(),
            account_slug=ACCOUNT_SLUG,
            fetched_at=datetime(2026, 8, 16, tzinfo=UTC),
        )
        b = to_raw_message(
            _pushed(),
            account_slug=ACCOUNT_SLUG,
            fetched_at=datetime(2026, 8, 17, tzinfo=UTC),
        )
        assert storage_key(a) == storage_key(b)

    def test_timezone_normalised_before_render(self):
        # LinkedIn timestamps arrive as epoch ms; a client rendering them in a
        # local offset must not re-key the message.
        local = _pushed(
            sent_at=datetime(
                2026, 8, 15, 10, 30, 12, tzinfo=timezone(timedelta(hours=-4))
            )
        )
        a = to_raw_message(local, account_slug=ACCOUNT_SLUG)
        b = to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)
        assert a.payload == b.payload

    def test_storage_key_buckets_by_sent_date(self):
        raw = to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)
        assert "/linkedin/2026/08/15/" in storage_key(raw)


class TestEnvelope:
    def test_inbound_parses_as_inbound(self):
        raw = to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)
        env = envelope.parse(raw.payload, raw.account, fallback=raw.fetched_at)
        assert env.direction == "inbound"
        assert env.sender_address == "jane-recruiter@linkedin.invalid"
        assert env.sender_domain == "linkedin.invalid"
        assert env.sent_at == datetime(2026, 8, 15, 14, 30, 12, tzinfo=UTC)

    def test_outbound_parses_as_outbound(self):
        raw = to_raw_message(
            _pushed(direction="outbound"), account_slug=ACCOUNT_SLUG
        )
        env = envelope.parse(raw.payload, raw.account, fallback=raw.fetched_at)
        assert env.direction == "outbound"
        assert env.sender_address == account_address(ACCOUNT_SLUG)

    def test_body_text_returns_full_text(self):
        text = "Hello — víd unicode ✨ and\nseveral\nlines. " * 40
        raw = to_raw_message(_pushed(text=text), account_slug=ACCOUNT_SLUG)
        assert envelope.body_text(raw.payload).strip() == text.strip()

    def test_display_name_with_quotes_survives_parse(self):
        raw = to_raw_message(
            _pushed(partner_name='Jane "JJ" O\'Recruiter'),
            account_slug=ACCOUNT_SLUG,
        )
        env = envelope.parse(raw.payload, raw.account, fallback=raw.fetched_at)
        assert env.sender_address == "jane-recruiter@linkedin.invalid"


class TestAddresses:
    def test_slug_maps_to_invalid_domain(self):
        assert synthetic_address("Jane-Recruiter") == "jane-recruiter@linkedin.invalid"

    def test_urn_characters_collapse(self):
        addr = synthetic_address("urn:li:msg_message:2-MTc1")
        local = addr.split("@")[0]
        assert " " not in local and ":" not in local

    def test_empty_identifier_raises(self):
        with pytest.raises(PushError):
            synthetic_address("::")

    def test_unknown_direction_raises(self):
        with pytest.raises(PushError):
            to_raw_message(_pushed(direction="sideways"), account_slug=ACCOUNT_SLUG)


class _MemoryStorage:
    """Write-once dict storage, the Storage slice ingest touches."""

    def __init__(self):
        self.objects: dict[str, RawMessage] = {}

    def key_for(self, message: RawMessage) -> str:
        return storage_key(message)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put(self, message: RawMessage) -> str:
        key = storage_key(message)
        self.objects.setdefault(key, message)
        return key


class _MemoryMessages:
    def __init__(self):
        self.rows: dict[str, object] = {}

    def by_storage_key(self, storage_key: str):
        return self.rows.get(storage_key)

    def add(self, message):
        self.rows[message.storage_key] = message
        return message

    def known_external_ids(self, channel, account, external_ids):
        return set()


class TestIngestRaw:
    def test_second_push_is_a_noop(self):
        storage, messages = _MemoryStorage(), _MemoryMessages()
        raws = [to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)]

        first = ingest_raw(
            raws,
            storage=storage,
            messages=messages,
            channel="linkedin",
            account=account_address(ACCOUNT_SLUG),
        )
        again = ingest_raw(
            [to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)],
            storage=storage,
            messages=messages,
            channel="linkedin",
            account=account_address(ACCOUNT_SLUG),
        )

        assert first.stored == 1 and first.rows_inserted == 1
        assert again.is_noop
        assert again.already_stored == 1 and again.rows_existing == 1

    def test_row_carries_channel_and_thread(self):
        storage, messages = _MemoryStorage(), _MemoryMessages()
        ingest_raw(
            [to_raw_message(_pushed(), account_slug=ACCOUNT_SLUG)],
            storage=storage,
            messages=messages,
            channel="linkedin",
            account=account_address(ACCOUNT_SLUG),
        )
        (row,) = messages.rows.values()
        assert row.channel == "linkedin"
        assert row.thread_id == "urn:li:msg_conversation:(urn:li:fsd_profile:ACo,2-YZ)"
        assert row.direction == "inbound"
