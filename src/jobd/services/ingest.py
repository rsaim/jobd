"""Ingestion: source → raw storage → the record. Idempotent by construction.

The ordering is the point, and it is not negotiable:

1. **Write to raw storage first.** Durably, before anything derives from it.
   I3 promises the record is rebuildable from raw; a row whose bytes were never
   stored breaks that promise permanently, and no amount of later care fixes it.
2. **Then insert the row**, keyed by the same content hash.

Crash between them and the next run stores nothing new (write-once) and inserts
the missing row. Crash before them and the next run does both. There is no
ordering of failures that produces a row without its bytes.

Idempotency is structural rather than checked: the storage key is a content
hash, storage is write-once, and `message.storage_key` is UNIQUE. Running an
import twice is a no-op by construction, which is what I2 asks for.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import psycopg

from jobd.domain.envelope import parse
from jobd.domain.raw import RawMessage
from jobd.domain.record import Channel, Message
from jobd.ports import Storage


class MessageWriter(Protocol):
    """The slice of `MessageRepository` this service needs.

    Narrower than the repository on purpose: a service that accepts the whole
    class can grow a dependency on any of it, and this one has no business
    reading companies.
    """

    def by_storage_key(self, storage_key: str) -> Message | None: ...
    def add(self, message: Message) -> Message: ...
    def known_external_ids(
        self, channel: str, account: str, external_ids: list[str]
    ) -> set[str]: ...


class DaySource(Protocol):
    """The slice of `GmailSource` the day-batched path needs.

    Separate from `Source`: history-cursor incremental sync has no notion of
    "a day", and a source without one (a future LinkedIn adapter, say) simply
    would not implement this half.
    """

    name: str

    def list_ids(self, account: str, day: date) -> Iterator[str]: ...
    def get_one(self, account: str, message_id: str) -> RawMessage: ...


class Source(Protocol):
    """The slice of `MessageSource` this service needs."""

    name: str

    def fetch(
        self,
        account: str,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> Iterator[RawMessage]: ...

    def checkpoint(self, account: str) -> str | None: ...


@dataclass(slots=True)
class IngestResult:
    """What one run did. Every field is observable, because M4 gate 3 is."""

    account: str
    fetched: int = 0
    #: Objects newly written to raw storage.
    stored: int = 0
    #: Already in raw storage — the count that must equal `fetched` on a re-run.
    already_stored: int = 0
    rows_inserted: int = 0
    rows_existing: int = 0
    #: Ids skipped before any fetch, because `known_external_ids` already had
    #: them. What makes resuming a killed day-worker cheap instead of a full
    #: re-download (M4 gate 3's day-batched half).
    skipped_known: int = 0
    cursor: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        """True when the run changed nothing — the shape of a second import."""
        return self.stored == 0 and self.rows_inserted == 0


def read_cursor(conn: psycopg.Connection[Any], source: str, account: str) -> str | None:
    """The stored resume point, or None for a full sync."""
    row = conn.execute(
        "SELECT cursor FROM ingest_cursor WHERE source = %s AND account = %s",
        (source, account),
    ).fetchone()
    return None if row is None else str(row[0])


def write_cursor(
    conn: psycopg.Connection[Any], source: str, account: str, cursor: str
) -> None:
    """Record where the next run resumes from."""
    conn.execute(
        "INSERT INTO ingest_cursor (source, account, cursor) VALUES (%s, %s, %s)"
        " ON CONFLICT (source, account) DO UPDATE SET"
        "   cursor = EXCLUDED.cursor, updated_at = now()",
        (source, account, cursor),
    )


def ingest_account(
    *,
    source: Source,
    storage: Storage,
    messages: MessageWriter,
    conn: psycopg.Connection[Any],
    account: str,
    since: datetime | None = None,
    channel: Channel = "email",
    resume: bool = True,
) -> IngestResult:
    """Ingest one account. Returns counts, and never raises for one bad message.

    Args:
        resume: Use the stored cursor when one exists. `False` forces a full
            sync — the recovery path when Gmail's history has aged out.

    A single malformed message is recorded in ``result.errors`` and skipped
    rather than aborting: a five-year archive contains mail from every broken
    client ever written, and one of them must not cost the other 40,000.
    """
    result = IngestResult(account=account)
    cursor = read_cursor(conn, source.name, account) if resume else None

    for raw in source.fetch(account, since=since, cursor=cursor):
        result.fetched += 1
        try:
            _one(raw, storage=storage, messages=messages, channel=channel, out=result)
        except Exception as exc:  # one bad message is not fatal — see below
            result.errors.append(f"{raw.external_id}: {type(exc).__name__}: {exc}")

    checkpoint = source.checkpoint(account)
    if checkpoint:
        write_cursor(conn, source.name, account, checkpoint)
        result.cursor = checkpoint
    return result


def ingest_raw(
    raws: Iterable[RawMessage],
    *,
    storage: Storage,
    messages: MessageWriter,
    channel: Channel,
    account: str,
) -> IngestResult:
    """Ingest messages something else already fetched.

    The push half of ingestion: the LinkedIn companion extension delivers
    batches over HTTP, so there is no ``Source`` to pull from and no cursor to
    store — the extension keeps its own watermark. Same ``_one`` per message
    as the pull paths, so the storage-before-row ordering and its crash
    guarantees hold here without restating them.

    A single malformed message is recorded in ``result.errors`` and skipped,
    not fatal — same reasoning as :func:`ingest_account`.
    """
    result = IngestResult(account=account)
    for raw in raws:
        result.fetched += 1
        try:
            _one(raw, storage=storage, messages=messages, channel=channel, out=result)
        except Exception as exc:  # one bad message is not fatal — see above
            result.errors.append(f"{raw.external_id}: {type(exc).__name__}: {exc}")
    return result


def ingest_day(
    *,
    source: DaySource,
    storage: Storage,
    messages: MessageWriter,
    account: str,
    day: date,
    channel: Channel = "email",
) -> IngestResult:
    """Ingest one calendar day (UTC) for one account.

    The unit of work day-batched backfill is built from. Two properties make a
    killed worker resumable rather than a reason to start the day over:

    * Listing ids is separated from fetching them, and every id already known
      (``known_external_ids``) is skipped *before* the expensive fetch. A
      worker killed on message 400 of 500 re-lists on restart, skips the first
      400 for the cost of one query, and only fetches the rest.
    * Each message is written through to storage and the row table
      immediately (via ``_one``, shared with the whole-range path), so
      whatever a killed worker finished is durably there regardless of
      whether the process reached the end of the day.

    A single malformed message is recorded in ``result.errors`` and skipped,
    not fatal — same reasoning as :func:`ingest_account`.
    """
    result = IngestResult(account=account)

    ids = list(source.list_ids(account, day))
    known = messages.known_external_ids(channel, account, ids)
    result.skipped_known = len(known)

    for message_id in ids:
        if message_id in known:
            continue
        try:
            raw = source.get_one(account, message_id)
            result.fetched += 1
            _one(raw, storage=storage, messages=messages, channel=channel, out=result)
        except Exception as exc:  # one bad message is not fatal — see above
            result.errors.append(f"{message_id}: {type(exc).__name__}: {exc}")

    return result


#: RFC 5322 headers preserved verbatim (truncated) into `raw_headers` —
#: the ones with plausible future signal that don't earn a column yet.
_KEPT_HEADERS = (
    "Return-Path",
    "Delivered-To",
    "Authentication-Results",
    "Received-SPF",
    "List-Unsubscribe",
    "Precedence",
    "X-Mailer",
    "X-SG-EID",
    "X-Entity-ID",
    "X-Mailgun-Sid",
    "X-SES-Outgoing",
)


def extract_envelope_extras(raw: RawMessage) -> dict[str, Any]:
    """Everything the raw message carries beyond the Message row's own
    columns (migration 0026): the source sidecar verbatim, the RFC threading
    identities, the real-correspondent headers, the bulk markers. Shared by
    the ingest write path and `backfill_envelopes` so both fill identical
    shapes. Pure parsing, never raises — a malformed payload yields Nones."""
    from email import message_from_bytes, policy

    extras: dict[str, Any] = {
        "raw_metadata": dict(raw.metadata) if raw.metadata else None,
        "reply_to": None,
        "cc_addresses": None,
        "message_id_header": None,
        "in_reply_to": None,
        "references_ids": None,
        "is_bulk": None,
        "raw_headers": None,
    }
    try:
        parsed = message_from_bytes(raw.payload, policy=policy.default)
    except Exception:
        return extras

    def header(name: str) -> str | None:
        try:
            value = str(parsed.get(name) or "").strip()
        except Exception:
            return None
        return value or None

    extras["reply_to"] = header("Reply-To")
    cc = header("Cc")
    if cc:
        extras["cc_addresses"] = [a.strip() for a in cc.split(",") if a.strip()]
    extras["message_id_header"] = header("Message-ID")
    extras["in_reply_to"] = header("In-Reply-To")
    refs = header("References")
    if refs:
        extras["references_ids"] = refs.split()
    precedence = (header("Precedence") or "").lower()
    extras["is_bulk"] = bool(header("List-Unsubscribe")) or precedence in (
        "bulk",
        "list",
    )
    kept = {
        name: value[:500]
        for name in _KEPT_HEADERS
        if (value := header(name)) is not None
    }
    extras["raw_headers"] = kept or None
    return extras


def _one(
    raw: RawMessage,
    *,
    storage: Storage,
    messages: MessageWriter,
    channel: Channel,
    out: IngestResult,
) -> None:
    key = storage.key_for(raw)

    # `exists` before `put` is for the counter, not for correctness — `put` is
    # write-once on its own. Without it a re-run could not report "nothing new",
    # and "it printed 0" is how gate 2 is checked by a human.
    if storage.exists(key):
        out.already_stored += 1
    else:
        out.stored += 1
    storage.put(raw)

    if messages.by_storage_key(key) is not None:
        out.rows_existing += 1
        return

    envelope = parse(raw.payload, raw.account, fallback=raw.fetched_at)
    row = messages.add(
        Message(
            storage_key=key,
            channel=channel,
            account=raw.account,
            external_id=raw.external_id,
            thread_id=raw.metadata.get("thread_id") or None,
            sender_address=envelope.sender_address,
            sender_domain=envelope.sender_domain,
            recipient_addresses=envelope.recipient_addresses,
            direction=envelope.direction,
            sent_at=envelope.sent_at,
            subject=envelope.subject,
            # Body extraction is M5's, in the same pass that classifies. See
            # jobd.domain.envelope for why an empty column beats a half-filled
            # one here.
            body_text="",
        )
    )
    if row.id is not None and hasattr(messages, "set_envelope_extras"):
        messages.set_envelope_extras(row.id, **extract_envelope_extras(raw))
    out.rows_inserted += 1


def backfill_envelopes(
    conn: Any,
    storage: Storage,
    *,
    limit: int = 100_000,
    workers: int = 32,
    meter: Any = None,
) -> dict[str, int]:
    """Fill NULL envelope columns on existing rows from the raw store.

    The v2 scrape writer inserted message rows without parsing envelopes
    (58k rows with a NULL sender_address on the reference record), which
    starves every sender-keyed layer: learned rules can't match, the
    prefilter can't score, and the review queue clusters by "(none)".
    The same gap left `thread_id` empty on 98.9%% of rows, which silently
    disabled thread-carry — the zero-cost path that links content-free
    replies — so it is re-derived here too, from the raw metadata Gmail's
    threadId was stored in.
    Raw bytes are the source of truth and were stored write-once, so this
    is a pure re-derive: fetch, parse, UPDATE the missing fields only.

    Queue-pending messages sort first so the review page benefits from the
    first minute of a long run, not the last.
    """
    from concurrent.futures import ThreadPoolExecutor

    from jobd.domain.envelope import parse

    rows = conn.execute(
        """
        SELECT id, storage_key, account, created_at
        FROM message
        WHERE (sender_address IS NULL
               OR thread_id IS NULL OR thread_id = ''
               OR raw_metadata IS NULL)
          AND storage_key IS NOT NULL
        ORDER BY EXISTS (SELECT 1 FROM review_queue rq
                         WHERE rq.message_id = message.id
                           AND rq.status = 'pending') DESC,
                 sent_at DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    meter.set_total(len(rows))
    counts = {"seen": 0, "updated": 0, "errors": 0}

    def fetch(row: Any) -> tuple[Any, Any | None]:
        try:
            return row, storage.get(row[1])
        except Exception:
            return row, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row, raw in pool.map(fetch, rows):
            counts["seen"] += 1
            if raw is None:
                counts["errors"] += 1
                meter.bump("fetch_failed")
                meter.error(f"fetch failed: {row[1]}")
                continue
            message_id, _key, account, created_at = row
            try:
                env = parse(raw.payload, account, fallback=created_at)
            except Exception:
                counts["errors"] += 1
                meter.bump("parse_failed")
                continue
            thread = (raw.metadata or {}).get("thread_id") or None
            extras = extract_envelope_extras(raw)
            from psycopg.types.json import Jsonb

            conn.execute(
                """
                UPDATE message SET
                    sender_address = coalesce(sender_address, %s),
                    sender_domain = coalesce(sender_domain, %s),
                    recipient_addresses = coalesce(recipient_addresses, %s),
                    thread_id = coalesce(nullif(thread_id, ''), %s),
                    raw_metadata = coalesce(raw_metadata, %s),
                    reply_to = coalesce(reply_to, %s),
                    cc_addresses = coalesce(cc_addresses, %s),
                    message_id_header = coalesce(message_id_header, %s),
                    in_reply_to = coalesce(in_reply_to, %s),
                    references_ids = coalesce(references_ids, %s),
                    is_bulk = coalesce(is_bulk, %s),
                    raw_headers = coalesce(raw_headers, %s)
                WHERE id = %s
                """,
                (env.sender_address, env.sender_domain,
                 list(env.recipient_addresses or []), thread,
                 Jsonb(extras["raw_metadata"]) if extras["raw_metadata"] else None,
                 extras["reply_to"], extras["cc_addresses"],
                 extras["message_id_header"], extras["in_reply_to"],
                 extras["references_ids"], extras["is_bulk"],
                 Jsonb(extras["raw_headers"]) if extras["raw_headers"] else None,
                 message_id),
            )
            counts["updated"] += 1
            meter.bump("updated")
            if counts["updated"] % 500 == 0:
                conn.commit()
    conn.commit()
    meter.flush(force=True)
    return counts
