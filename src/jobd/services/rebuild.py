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
) -> RebuildResult:
    """Drop the derived store and rebuild it from the archive.

    Args:
        classify: Skip to restore only the message rows — useful when the point
            is to check that the archive is readable, without paying for a
            model pass.
    """
    result = RebuildResult()

    conn.execute(f"TRUNCATE {', '.join(DERIVED_TABLES)} CASCADE")

    for key in storage.iter_keys(prefix):
        result.keys_seen += 1
        try:
            raw = storage.get(key)
        except Exception as exc:
            # A single corrupt object must not stop a rebuild; it must also not
            # vanish silently, because the archive is the source of truth and
            # an unreadable object there is the most serious thing this system
            # can discover.
            result.unreadable.append(f"{key}: {type(exc).__name__}: {exc}")
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
        "deterministic_calls",
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
    ):
        setattr(total, name, getattr(total, name) + getattr(batch, name))
    total.errors.extend(batch.errors)
