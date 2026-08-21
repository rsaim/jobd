"""Content-hash keys for raw storage (PRD P1, M3 gate 4).

This is the function that makes ingestion idempotent *by construction* rather
than by a dedupe pass: the same message hashes to the same key, so a re-run
addresses the object it already wrote instead of creating a second one.

Three properties it must have, and the reasons they are not negotiable:

**Stable across processes and runs.** Python's ``hash()`` is salted per process
and would produce a different key every restart, silently duplicating a
five-year backfill. SHA-256 is used for that reason, not for a security one.

**Independent of when it was fetched.** ``fetched_at`` differs on every run, so
it is deliberately outside the hash. It is recorded *inside* the stored envelope
as provenance — see :func:`jobd.domain.raw.encode`.

**Independent of the source's own id.** A provider that renumbers, or a LinkedIn
archive re-exported next year with fresh ids, must not produce a second copy of
a message already stored. ``external_id`` is provenance too, not identity.

What *is* in the hash: source, account, and the payload bytes. Account is in
because the same recruiter mail landing in two of the user's inboxes is two real
arrivals; collapsing them at the raw layer would destroy evidence the record
layer needs to merge properly (M4's multi-account question).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from jobd.domain.raw import RawMessage

#: Bump only for a change that alters the bytes fed to SHA-256. Every existing
#: key would move, so a bump means a full re-ingest, not a migration.
HASH_SCHEME = "jobd-raw-v1"

#: Lifecycle rules in infra/terraform/storage target this prefix. Changing it
#: silently opts every future object out of the STANDARD_IA transition.
RAW_PREFIX = "raw"


def _framed(*parts: bytes) -> bytes:
    """Length-prefix each part so concatenation cannot be ambiguous.

    Without framing, ``("gmail", "ab")`` and ``("gmaila", "b")`` hash
    identically. With an 8-byte big-endian length before each part, they cannot.
    """
    out = bytearray()
    for part in parts:
        out += len(part).to_bytes(8, "big")
        out += part
    return bytes(out)


def content_hash(source: str, account: str, payload: bytes) -> str:
    """Return the hex SHA-256 that identifies this message's content."""
    framed = _framed(
        HASH_SCHEME.encode("utf-8"),
        source.encode("utf-8"),
        account.encode("utf-8"),
        payload,
    )
    return hashlib.sha256(framed).hexdigest()


#: Metadata key a source may set to place a message under a real date rather
#: than ``unknown-date``. Full ISO-8601 datetime (any offset; UTC preferred),
#: e.g. Gmail's own ``internalDate`` converted to a timestamp.
OCCURRED_AT_META_KEY = "occurred_at"


def storage_key(message: RawMessage) -> str:
    """Return the storage key:
    ``raw/<source>/<YYYY>/<MM>/<DD>/<HHMMSS>_<sha256>.jsonl``.

    The date/time is for a human skimming the bucket during debugging —
    finding "that message from March" should mean opening a folder, not
    grepping thousands of hash shards. It comes from the message's own
    ``occurred_at`` metadata (the source's timestamp for when the message
    happened), never from when jobd fetched it.

    That distinction is load-bearing, not cosmetic: storage is write-once and
    idempotency depends on the same message always producing the same key
    (I2). ``fetched_at`` is different on every run by definition, so keying on
    it would silently duplicate every message on a second ingest. For the same
    reason this is a *time*-of-day sort, not a per-day sequence number: "the
    17th message ingested today" depends on what has already been written and
    in what order, which a re-run or an out-of-order backfill cannot promise
    to reproduce. Time-of-day needs no such state and sorts the same way. A
    source with no reliable timestamp lands under ``unknown-date``.

    The full hash stays in the filename rather than a short prefix: two real
    messages a person receives in the same second are rare but not
    impossible, and a truncated hash would reintroduce exactly the collision
    risk content-addressing exists to remove.

    ``.jsonl`` because the envelope is one JSON object on one line. Single-line
    files concatenate into a valid JSONL stream, which is what makes a bulk
    re-derive cheap.
    """
    digest = content_hash(message.source, message.account, message.payload)
    occurred = message.metadata.get(OCCURRED_AT_META_KEY)
    year, month, day, hhmmss = _occurred_parts(occurred)
    return f"{RAW_PREFIX}/{message.source}/{year}/{month}/{day}/{hhmmss}_{digest}.jsonl"


def _occurred_parts(occurred_at: str | None) -> tuple[str, str, str, str]:
    """ISO datetime -> ``(YYYY, MM, DD, HHMMSS)``. Unparsable or missing ->
    ``unknown-date`` for the path, ``000000`` for the sort key — never raises,
    since a key function must not fail a write over metadata that is
    provenance, not correctness."""
    if occurred_at:
        try:
            dt = datetime.fromisoformat(occurred_at)
        except ValueError:
            dt = None
        if dt is not None:
            return (
                f"{dt.year:04d}",
                f"{dt.month:02d}",
                f"{dt.day:02d}",
                dt.strftime("%H%M%S"),
            )
    return "unknown-date", "unknown-date", "unknown-date", "000000"
