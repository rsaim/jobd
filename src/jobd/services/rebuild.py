"""`jobd rebuild` — re-derive the whole record from raw storage (I3).

The invariant this exists to make real: *"Improving a prompt and re-deriving the
world is a routine operation, not a migration."* Routine means one command, no
manual steps, and no re-fetching — Gmail is not contacted, the archive already
has the bytes.

What it drops is everything derived: companies, applications, stage events,
messages, contacts, and the review queue. What it keeps is `ingest_cursor`,
because a cursor is operational state rather than a derived artifact — dropping
it would force a full re-sync of a mailbox whose bytes are already on disk.

The safety argument is entirely upstream. This is destructive to Postgres and
harmless overall *only because* raw storage is the source of truth and the
runtime credential cannot delete from it (SECURITY.md §2). If either of those
stopped being true, this command would be a data-loss bug.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import psycopg

from jobd.domain.envelope import parse
from jobd.domain.record import Channel, Message
from jobd.ports import LLMProvider, Storage
from jobd.services.classify import ClassifyResult, Repositories, classify_pending

#: Truncated in dependency order, in one statement so no foreign key is ever
#: transiently violated. `ingest_cursor` is deliberately absent.
DERIVED_TABLES = (
    "review_queue",
    "stage_event",
    "message",
    "application",
    "contact_company",
    "contact_identity",
    "contact",
    "company_alias",
    "company",
)


@dataclass(slots=True)
class RebuildResult:
    keys_seen: int = 0
    messages_restored: int = 0
    unreadable: list[str] = field(default_factory=list)
    classification: ClassifyResult | None = None


@dataclass(slots=True)
class ImportResult:
    """What `import_store` did — additive, never destructive."""

    keys_seen: int = 0
    messages_inserted: int = 0
    already_present: int = 0
    unreadable: list[str] = field(default_factory=list)


def import_store(
    *,
    storage: Storage,
    repos: Repositories,
    conn: psycopg.Connection[Any],
    prefix: str = "raw/",
    channel: Channel = "email",
    workers: int = 32,
    batch: int = 1000,
) -> ImportResult:
    """Ingest a raw archive into the record — idempotent and resumable.

    Unlike `rebuild` (which TRUNCATEs first), this only adds the rows that are
    missing, keyed by the content-hash `storage_key` — the right shape for the
    first load of a raw dump that some other tool already wrote to the archive,
    and safe to re-run: a killed import resumes where it left off rather than
    starting over. Commits per batch, so partial progress is durable.
    """
    from concurrent.futures import ThreadPoolExecutor

    from jobd.domain.envelope import parse
    from jobd.services.ingest import extract_envelope_extras

    result = ImportResult()
    keys = list(storage.iter_keys(prefix))
    result.keys_seen = len(keys)
    known = getattr(repos.messages, "known_storage_keys", None)
    existing = known(keys) if known is not None else set()
    result.already_present = len(existing)
    todo = [k for k in keys if k not in existing]

    def fetch(key: str) -> tuple[str, Any]:
        try:
            return key, storage.get(key)
        except Exception as exc:  # one bad object must not stop the import
            return key, exc

    for start in range(0, len(todo), batch):
        chunk = todo[start : start + batch]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            fetched = list(pool.map(fetch, chunk))
        for key, raw in fetched:
            if isinstance(raw, Exception):
                result.unreadable.append(f"{key}: {type(raw).__name__}: {raw}")
                continue
            envelope = parse(raw.payload, raw.account, fallback=raw.fetched_at)
            row = repos.messages.add(
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
                    body_text="",
                )
            )
            if row.id is not None and hasattr(repos.messages, "set_envelope_extras"):
                repos.messages.set_envelope_extras(row.id, **extract_envelope_extras(raw))
            result.messages_inserted += 1
        conn.commit()

    return result


def rebuild(
    *,
    storage: Storage,
    llm: LLMProvider,
    repos: Repositories,
    conn: psycopg.Connection[Any],
    prefix: str = "raw/",
    channel: Channel = "email",
    classify: bool = True,
    batch: int = 500,
    workers: int = 32,
) -> RebuildResult:
    """Drop the derived store and rebuild it from the archive.

    Args:
        classify: Skip to restore only the message rows — useful when the point
            is to check that the archive is readable, without paying for a
            model pass.
        workers: Thread-pool size for the raw fetch. The fetch is an S3 round
            trip per message and I/O-bound, so a pool buys real parallelism
            (the same profile `classify_pending`'s `_prefetch` and
            `backfill_envelopes` exploit). DB writes stay sequential on the
            main thread — a psycopg connection is not thread-safe. 1 restores
            the plain sequential loop.
    """
    result = RebuildResult()

    conn.execute(f"TRUNCATE {', '.join(DERIVED_TABLES)} CASCADE")

    # Materialise the key list up front so the fetch can be parallelised. The
    # archive is content-addressed, so the list is a few megabytes of strings
    # even for a five-year mailbox.
    keys = list(storage.iter_keys(prefix))

    def fetch(key: str) -> tuple[str, Any]:
        try:
            return key, storage.get(key)
        except Exception as exc:  # a corrupt object must not stop the rebuild
            return key, exc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for key, raw in pool.map(fetch, keys):
            result.keys_seen += 1
            if isinstance(raw, Exception):
                # A single corrupt object must not stop a rebuild; it must also
                # not vanish silently, because the archive is the source of
                # truth and an unreadable object there is the most serious thing
                # this system can discover.
                result.unreadable.append(f"{key}: {type(raw).__name__}: {raw}")
                continue

            envelope = parse(raw.payload, raw.account, fallback=raw.fetched_at)
            repos.messages.add(
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
                    body_text="",
                )
            )
            result.messages_restored += 1

    conn.commit()

    if classify:
        totals = ClassifyResult()
        while True:
            batch_result = classify_pending(
                storage=storage, llm=llm, repos=repos, conn=conn, limit=batch
            )
            if batch_result.seen == 0:
                break
            _accumulate(totals, batch_result)
        result.classification = totals

    return result


def _accumulate(total: ClassifyResult, batch: ClassifyResult) -> None:
    for name in (
        "seen",
        "filtered_out",
        "carried_forward",
        "llm_calls",
        "escalated",
        "not_job_related",
        "queued_for_review",
        "recorded",
        "companies_created",
        "applications_created",
        "stage_events",
        "rules_learned",
        "rules_demoted",
        "explored",
        "terminal_routed",
        "stage_conflicts",
        "thread_llm_reused",
    ):
        setattr(total, name, getattr(total, name) + getattr(batch, name))
    total.errors.extend(batch.errors)
