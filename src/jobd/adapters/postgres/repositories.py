"""Repositories over the derived store.

One per aggregate, plain SQL, no ORM. Each takes an open connection rather than
opening its own, so a caller can put several writes in one transaction — a
message and the stage event it evidences must land together or not at all.

Reads return domain objects from :mod:`jobd.domain.record`; nothing in this
module leaks a psycopg row upward.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg

from jobd.domain.prefilter import GENERIC_DOMAINS
from jobd.domain.record import (
    Application,
    Company,
    CompanyAlias,
    CompanyVerification,
    Contact,
    ContactIdentity,
    Message,
    PromptSnippet,
    SenderRule,
    StageEvent,
)


class Repository:
    """Common base: holds the connection, nothing more."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def _one(self, sql: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
        return self._conn.execute(sql, params).fetchone()

    def _id(self, sql: str, params: tuple[Any, ...]) -> UUID:
        row = self._one(sql, params)
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise RuntimeError("INSERT ... RETURNING produced no row")
        return UUID(str(row[0]))


class CompanyRepository(Repository):
    """Companies and their aliases."""

    def add(self, company: Company) -> Company:
        new_id = self._id(
            "INSERT INTO company (canonical_name, domain, first_seen_at,"
            " last_seen_at, kind)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                company.canonical_name,
                company.domain,
                company.first_seen_at,
                company.last_seen_at,
                company.kind,
            ),
        )
        return Company(**{**_fields(company), "id": new_id})

    def get(self, company_id: UUID) -> Company | None:
        row = self._one(
            "SELECT id, canonical_name, domain, first_seen_at, last_seen_at, kind"
            " FROM company WHERE id = %s",
            (company_id,),
        )
        return None if row is None else _company(row)

    def by_domain(self, domain: str) -> Company | None:
        """Domain match — the strongest entity-resolution signal there is."""
        row = self._one(
            "SELECT id, canonical_name, domain, first_seen_at, last_seen_at, kind"
            " FROM company WHERE lower(domain) = lower(%s)",
            (domain,),
        )
        return None if row is None else _company(row)

    def by_alias(self, alias: str) -> Company | None:
        """Resolve a company through any of its names, canonical or aliased."""
        row = self._one(
            "SELECT c.id, c.canonical_name, c.domain, c.first_seen_at,"
            " c.last_seen_at, c.kind"
            " FROM company c JOIN company_alias a ON a.company_id = c.id"
            " WHERE lower(a.alias) = lower(%s)",
            (alias,),
        )
        return None if row is None else _company(row)

    def add_alias(self, alias: CompanyAlias) -> CompanyAlias:
        new_id = self._id(
            "INSERT INTO company_alias (company_id, alias, source)"
            " VALUES (%s, %s, %s) RETURNING id",
            (alias.company_id, alias.alias, alias.source),
        )
        return CompanyAlias(**{**_fields(alias), "id": new_id})

    def widen(self, company_id: UUID | None, when: datetime) -> None:
        """Stretch first/last seen to include a new message.

        LEAST/GREATEST rather than a plain assignment: classification does not
        run in date order after the first pass, so an older message arriving
        later must move `first_seen_at` back, not forward.
        """
        if company_id is None:
            return
        self._conn.execute(
            "UPDATE company SET"
            "   first_seen_at = LEAST(first_seen_at, %s),"
            "   last_seen_at = GREATEST(last_seen_at, %s),"
            "   updated_at = now()"
            " WHERE id = %s",
            (when, when, company_id),
        )

    def all(self) -> list[Company]:
        """Every discovered company, most recently touched first."""
        rows = self._conn.execute(
            "SELECT id, canonical_name, domain, first_seen_at, last_seen_at, kind"
            " FROM company ORDER BY last_seen_at DESC"
        ).fetchall()
        return [_company(r) for r in rows]

    def delete(self, company_id: UUID) -> None:
        """Remove a company confirmed not job-related (`services/verify.py`).

        Cascades take applications (and, via those, stage_event — migration
        0001's chain), aliases, contact_company and message_company links.
        `sender_rule`/`sender_category` rows referencing it fall back to a
        bare NULL company_id rather than being deleted (migrations 0008/
        0009's ON DELETE SET NULL) — a rule is a fact about a *domain*,
        independent of which company row it once resolved to, and a
        negative/undecided rule never needed the company_id to do its job
        anyway. `company_verification` rows survive too (migration 0016) —
        the audit trail that justified the deletion outlives what it
        audited, by design.
        """
        self._conn.execute("DELETE FROM company WHERE id = %s", (company_id,))

    def rename(
        self,
        company_id: UUID,
        *,
        canonical_name: str,
        kind: str | None = None,
        domain: str | None = None,
    ) -> None:
        """Correct a company's identity after `services/verify.py` confirms
        a better reading from the whole chain than the one-message guess
        that originally created it (`_resolve_company`'s fallback). `kind`
        and `domain` `None` leave the stored values untouched — a caller only
        passes them when the answer actually differs from what's on record.
        """
        sets = ["canonical_name = %s"]
        params: list[Any] = [canonical_name]
        if kind is not None:
            sets.append("kind = %s")
            params.append(kind)
        if domain is not None:
            sets.append("domain = %s")
            params.append(domain)
        params.append(company_id)
        self._conn.execute(
            f"UPDATE company SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
            tuple(params),
        )

    def ensure_alias(
        self, company_id: UUID, alias: str, source: str = "manual"
    ) -> None:
        """Record an alias, tolerating one that already exists.

        Renaming a company has to keep the old spelling reachable — the name
        the extractor wrote is the name the *next* message will carry, and
        `find_company`/`by_alias` are what match it. `add_alias` raises on the
        `lower(alias)` unique index, which is correct for a first write and
        wrong for "make sure this exists".
        """
        if not alias.strip():
            return
        self._conn.execute(
            "INSERT INTO company_alias (company_id, alias, source)"
            " VALUES (%s, %s, %s) ON CONFLICT (lower(alias)) DO NOTHING",
            (company_id, alias.strip(), source),
        )

    def aliases(self, company_id: UUID) -> list[CompanyAlias]:
        rows = self._conn.execute(
            "SELECT id, company_id, alias, source FROM company_alias"
            " WHERE company_id = %s ORDER BY alias",
            (company_id,),
        ).fetchall()
        return [
            CompanyAlias(
                id=UUID(str(r[0])),
                company_id=UUID(str(r[1])),
                alias=r[2],
                source=r[3],
            )
            for r in rows
        ]


class ContactRepository(Repository):
    """People, and the identities that resolve to them."""

    def add(self, contact: Contact) -> Contact:
        new_id = self._id(
            "INSERT INTO contact (display_name) VALUES (%s) RETURNING id",
            (contact.display_name,),
        )
        return Contact(display_name=contact.display_name, id=new_id)

    def get(self, contact_id: UUID) -> Contact | None:
        row = self._one(
            "SELECT id, display_name FROM contact WHERE id = %s", (contact_id,)
        )
        if row is None:
            return None
        return Contact(id=UUID(str(row[0])), display_name=row[1])

    def add_identity(self, identity: ContactIdentity) -> ContactIdentity:
        new_id = self._id(
            "INSERT INTO contact_identity (contact_id, channel, identifier)"
            " VALUES (%s, %s, %s) RETURNING id",
            (identity.contact_id, identity.channel, identity.identifier),
        )
        return ContactIdentity(**{**_fields(identity), "id": new_id})

    def by_identity(self, channel: str, identifier: str) -> Contact | None:
        """The lookup every ingest performs: is this address someone we know?"""
        row = self._one(
            "SELECT c.id, c.display_name FROM contact c"
            " JOIN contact_identity i ON i.contact_id = c.id"
            " WHERE i.channel = %s AND lower(i.identifier) = lower(%s)",
            (channel, identifier),
        )
        if row is None:
            return None
        return Contact(id=UUID(str(row[0])), display_name=row[1])

    def link_company(
        self,
        contact_id: UUID,
        company_id: UUID,
        *,
        role_title: str | None,
        first_seen_at: datetime,
        last_seen_at: datetime,
    ) -> None:
        """Associate a person with a company over a time window.

        Idempotent: a repeat link widens the window instead of failing, because
        every re-ingest of an old thread will call this again (I2).
        """
        self._conn.execute(
            "INSERT INTO contact_company"
            " (contact_id, company_id, role_title, first_seen_at, last_seen_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (contact_id, company_id) DO UPDATE SET"
            "   role_title = COALESCE(EXCLUDED.role_title, contact_company.role_title),"
            "   first_seen_at = LEAST("
            "       contact_company.first_seen_at, EXCLUDED.first_seen_at),"
            "   last_seen_at = GREATEST("
            "       contact_company.last_seen_at, EXCLUDED.last_seen_at)",
            (contact_id, company_id, role_title, first_seen_at, last_seen_at),
        )

    def companies_for(self, contact_id: UUID) -> list[UUID]:
        rows = self._conn.execute(
            "SELECT company_id FROM contact_company WHERE contact_id = %s"
            " ORDER BY first_seen_at",
            (contact_id,),
        ).fetchall()
        return [UUID(str(r[0])) for r in rows]


class ApplicationRepository(Repository):
    """Applications. Many per company, across years — no uniqueness anywhere."""

    def add(self, application: Application) -> Application:
        new_id = self._id(
            "INSERT INTO application"
            " (company_id, role_title, started_at, ended_at, outcome, source)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                application.company_id,
                application.role_title,
                application.started_at,
                application.ended_at,
                application.outcome,
                application.source,
            ),
        )
        return Application(**{**_fields(application), "id": new_id})

    def get(self, application_id: UUID) -> Application | None:
        row = self._one(
            "SELECT id, company_id, role_title, started_at, ended_at, outcome, source"
            " FROM application WHERE id = %s",
            (application_id,),
        )
        return None if row is None else _application(row)

    def set_role(self, application_id: UUID | None, role_title: str) -> None:
        """Name a previously untitled application.

        COALESCE, so a later message with a vaguer title cannot overwrite a
        specific one that an earlier message established.
        """
        if application_id is None:
            return
        self._conn.execute(
            "UPDATE application SET role_title = COALESCE(role_title, %s),"
            " updated_at = now() WHERE id = %s",
            (role_title, application_id),
        )

    def close(
        self, application_id: UUID | None, *, outcome: str, ended_at: datetime
    ) -> None:
        """Record a terminal outcome.

        Only fills an empty outcome. A rejection followed by a later "we would
        love to revisit" must not flip a closed application open, and a second
        terminal event must not overwrite the first — whichever ended it, ended
        it.
        """
        if application_id is None:
            return
        self._conn.execute(
            "UPDATE application SET"
            "   outcome = COALESCE(outcome, %s),"
            "   ended_at = COALESCE(ended_at, %s),"
            "   updated_at = now()"
            " WHERE id = %s",
            (outcome, ended_at, application_id),
        )

    def override_role(self, application_id: UUID, role_title: str | None) -> None:
        """Set a role title, overwriting whatever is there.

        The COALESCE in `set_role` is right for the extractor — a later,
        vaguer message must not clobber a specific title an earlier one
        established — and wrong for a person. A human correcting "Role not
        identified" (148 of 235 applications) or fixing a wrong title needs
        the write to land; a correction that silently does nothing is worse
        than no correction surface at all. Separate method rather than a flag
        so the two intents stay legible at the call site.
        """
        self._conn.execute(
            "UPDATE application SET role_title = %s, updated_at = now()"
            " WHERE id = %s",
            (role_title or None, application_id),
        )

    def set_outcome(
        self, application_id: UUID, *, outcome: str | None, ended_at: datetime | None
    ) -> None:
        """Set or clear an outcome, overwriting. The human counterpart to
        `close`, for the same reason `override_role` is `set_role`'s."""
        self._conn.execute(
            "UPDATE application SET outcome = %s, ended_at = %s, updated_at = now()"
            " WHERE id = %s",
            (outcome, ended_at, application_id),
        )

    def for_company(self, company_id: UUID) -> list[Application]:
        """Every application at a company, newest first. Never merged."""
        rows = self._conn.execute(
            "SELECT id, company_id, role_title, started_at, ended_at, outcome, source"
            " FROM application WHERE company_id = %s ORDER BY started_at DESC",
            (company_id,),
        ).fetchall()
        return [_application(r) for r in rows]


def _fanout_predicate(
    *,
    sender_domain: str | None = None,
    sender_address: str | None = None,
    recipient_address: str | None = None,
) -> tuple[str, list[Any]] | None:
    """The "every message sharing this attribute" match, written once.

    Returns the OR'd WHERE fragment and its parameters, or None when no match
    arg was given at all — which is a no-op by construction, not an error.
    Shared by `bulk_resolve`, `bulk_negative` and `count_fanout` so the count
    the dashboard previews and the rows the UPDATE touches cannot diverge.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if sender_domain:
        # Suffix match — `shein.com` also covers `market-us.shein.com` —
        # same rule `_match_rule` (classify.py) uses at classify time.
        clauses.append("(sender_domain = %s OR sender_domain LIKE %s)")
        params.extend([sender_domain, f"%.{sender_domain}"])
    if sender_address:
        clauses.append("lower(sender_address) = lower(%s)")
        params.append(sender_address)
    if recipient_address:
        clauses.append("%s = ANY(recipient_addresses)")
        params.append(recipient_address.lower())
    if not clauses:
        return None
    return " OR ".join(clauses), params


class MessageRepository(Repository):
    """Messages, keyed back to raw storage by content hash."""

    def add(self, message: Message) -> Message:
        new_id = self._id(
            "INSERT INTO message (storage_key, channel, account, external_id,"
            " thread_id, sender_address, sender_domain, recipient_addresses,"
            " direction, sent_at, subject, body_text, company_id,"
            " application_id, contact_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (
                message.storage_key,
                message.channel,
                message.account,
                message.external_id,
                message.thread_id,
                message.sender_address,
                message.sender_domain,
                list(message.recipient_addresses),
                message.direction,
                message.sent_at,
                message.subject,
                message.body_text,
                message.company_id,
                message.application_id,
                message.contact_id,
            ),
        )
        return Message(**{**_fields(message), "id": new_id})

    def set_envelope_extras(
        self,
        message_id: UUID,
        *,
        raw_metadata: dict[str, Any] | None,
        reply_to: str | None,
        cc_addresses: list[str] | None,
        message_id_header: str | None,
        in_reply_to: str | None,
        references_ids: list[str] | None,
        is_bulk: bool | None,
        raw_headers: dict[str, Any] | None,
    ) -> None:
        """The envelope facts beyond what `Message` itself models (migration
        0026) — one UPDATE shared by the ingest write path and the backfill,
        so a freshly scraped row and a repaired one are indistinguishable."""
        from psycopg.types.json import Jsonb

        self._conn.execute(
            """
            UPDATE message SET
                raw_metadata = %s, reply_to = %s, cc_addresses = %s,
                message_id_header = %s, in_reply_to = %s,
                references_ids = %s, is_bulk = %s, raw_headers = %s
            WHERE id = %s
            """,
            (
                Jsonb(raw_metadata) if raw_metadata else None,
                reply_to,
                cc_addresses,
                message_id_header,
                in_reply_to,
                references_ids,
                is_bulk,
                Jsonb(raw_headers) if raw_headers else None,
                message_id,
            ),
        )

    def get(self, message_id: UUID) -> Message | None:
        row = self._one(
            f"SELECT {_MESSAGE_COLS} FROM message WHERE id = %s", (message_id,)
        )
        return None if row is None else _message(row)

    def by_storage_key(self, storage_key: str) -> Message | None:
        """The idempotency check: has this exact content already been recorded?"""
        row = self._one(
            f"SELECT {_MESSAGE_COLS} FROM message WHERE storage_key = %s",
            (storage_key,),
        )
        return None if row is None else _message(row)

    def known_external_ids(
        self, channel: str, account: str, external_ids: list[str]
    ) -> set[str]:
        """Which of these source ids have already been ingested.

        Answers the question a resuming day-worker asks *before* the expensive
        per-message fetch: the content hash that `by_storage_key` checks needs
        the payload, and fetching the payload is exactly the cost resuming is
        meant to avoid. The source's own id is known from a cheap list call, so
        it is what resuming checks against instead.
        """
        if not external_ids:
            return set()
        rows = self._conn.execute(
            "SELECT external_id FROM message WHERE channel = %s AND account = %s"
            "  AND external_id = ANY(%s)",
            (channel, account, external_ids),
        ).fetchall()
        return {row[0] for row in rows}

    def for_company(self, company_id: UUID) -> list[Message]:
        rows = self._conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM message WHERE company_id = %s"
            " ORDER BY sent_at DESC",
            (company_id,),
        ).fetchall()
        return [_message(r) for r in rows]

    def for_company_all(self, company_id: UUID) -> list[Message]:
        """Every message touching this entity, primary or secondary link,
        oldest first — the input to `services/verify.py`'s whole-chain audit.

        Unlike `for_company` above (primary-link only, newest-first, for
        display), this also pulls in messages whose *secondary* link
        (`message_company`, role='agency') points here — the recruiting
        agency's own view of a chain must include messages where it was only
        the agency, not the resolved employer, matching the OR-EXISTS clause
        `dashboard.list_communications` already uses for the same relationship.
        Oldest first so a model reading the chain sees it in the order it
        happened, not reverse-chronological like the dashboard's own listing.
        """
        cols = ", ".join(f"m.{c.strip()}" for c in _MESSAGE_COLS.split(","))
        rows = self._conn.execute(
            f"SELECT {cols} FROM message m"
            " WHERE m.company_id = %s"
            "    OR EXISTS ("
            "         SELECT 1 FROM message_company mc"
            "         WHERE mc.message_id = m.id AND mc.role = 'agency'"
            "           AND mc.company_id = %s"
            "       )"
            " ORDER BY m.sent_at ASC",
            (company_id, company_id),
        ).fetchall()
        return [_message(r) for r in rows]

    def unclassified(
        self, limit: int = 500, partition: tuple[int, int] | None = None
    ) -> list[Message]:
        """Messages the classifier has not seen. Oldest first.

        Oldest first so a partial run leaves a contiguous classified prefix: a
        timeline built from the first half of a mailbox is incomplete but not
        misleading, where a random half is both.

        `partition=(k, n)` restricts to the k-th of n disjoint hash slices of
        the id space, so n classify processes can run concurrently on their
        own DB connections without racing each other for rows. Hash, not
        range: every slice stays oldest-first across the whole mailbox, and
        the slices stay balanced as rows are classified away. Thread-carry
        across slices is benign — the second process re-resolves or spends
        one extractor call the carry would have saved, never corrupts.
        """
        clause = "WHERE classified_at IS NULL"
        params: tuple[object, ...] = (limit,)
        if partition is not None:
            k, n = partition
            clause += " AND abs(hashtext(id::text)) %% %s = %s"
            params = (n, k, limit)
        rows = self._conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM message {clause}"
            " ORDER BY sent_at LIMIT %s",
            params,
        ).fetchall()
        return [_message(r) for r in rows]

    def unembedded(self, limit: int = 500) -> list[Message]:
        """Recorded messages with no embedding yet — `services/classify.py`'s
        `backfill_embeddings` work queue.

        `company_id IS NOT NULL` (not `classified_at IS NULL`, `unclassified`'s
        filter above) — embedding is only ever meaningful for a message that
        actually resolved to a company; a negative-filtered or still-queued
        one was never a `_record()` candidate and never will be without being
        reclassified first, at which point it goes through `classify --embed`
        the normal way, not this backfill. Oldest first, same reasoning as
        `unclassified`.
        """
        rows = self._conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM message"
            " WHERE company_id IS NOT NULL AND embedding IS NULL"
            " ORDER BY sent_at LIMIT %s",
            (limit,),
        ).fetchall()
        return [_message(r) for r in rows]

    def thread_context(
        self, thread_id: str
    ) -> tuple[UUID, UUID | None, UUID | None, UUID | None] | None:
        """(company_id, application_id, contact_id, agency_company_id) from
        the most recent already-recorded message in this thread, or None.

        The zero-cost half of `thread_has_company`: not just "does this
        thread resolve to a company" but "resolve *to what*", so a
        content-free reply ("Tuesday works, see you then") can be linked
        directly without asking any extractor a question it cannot answer
        from one message alone. Most recent by `sent_at` — a role can be
        added partway through a thread, and the latest record of it is the
        best guess for a message arriving even later in the same thread.

        The fourth element is the secondary agency link (migration 0013,
        `message_company`), if the most recent message had one — a
        correlated subquery, not a second round trip, so a thread that also
        involves a recruiting agency propagates that link forward too,
        exactly like the primary company/application/contact already do.
        """
        if not thread_id:
            return None
        row = self._one(
            "SELECT m.company_id, m.application_id, m.contact_id,"
            "  (SELECT mc.company_id FROM message_company mc"
            "    WHERE mc.message_id = m.id AND mc.role = 'agency' LIMIT 1)"
            " FROM message m"
            " WHERE m.thread_id = %s AND m.company_id IS NOT NULL"
            " ORDER BY m.sent_at DESC LIMIT 1",
            (thread_id,),
        )
        if row is None:
            return None
        return (
            UUID(str(row[0])), _uuid_or_none(row[1]),
            _uuid_or_none(row[2]), _uuid_or_none(row[3]),
        )

    def top_unresolved_correspondents(self, limit: int = 20) -> list[dict[str, Any]]:
        """Highest-leverage unknowns in the still-unclassified backlog.

        "Leverage" = how many messages resolving *this one correspondent*
        would settle at once — the graph-propagation entry point (see
        classify.py's module docstring): learn one recruiter's address and
        every message touching it, not just this one, is decided.

        Company domains are grouped by domain — a recruiter's colleague at
        the same firm is the same lead. Personal-mail-provider senders
        (:data:`GENERIC_DOMAINS`) are grouped by the exact address instead,
        since two `gmail.com` senders are two different people, not one.
        """
        generic = list(GENERIC_DOMAINS)
        rows = self._conn.execute(
            "SELECT sender_domain AS key, 'domain' AS kind, count(*) AS n,"
            "  (array_agg(subject ORDER BY sent_at DESC))[1] AS sample_subject"
            " FROM message"
            " WHERE classified_at IS NULL AND sender_domain IS NOT NULL"
            "   AND NOT (sender_domain = ANY(%s))"
            " GROUP BY sender_domain"
            " UNION ALL"
            " SELECT sender_address AS key, 'address' AS kind, count(*) AS n,"
            "  (array_agg(subject ORDER BY sent_at DESC))[1] AS sample_subject"
            " FROM message"
            " WHERE classified_at IS NULL AND sender_domain = ANY(%s)"
            "   AND sender_address IS NOT NULL"
            " GROUP BY sender_address"
            " ORDER BY n DESC LIMIT %s",
            (generic, generic, limit),
        ).fetchall()
        return [
            {"key": r[0], "kind": r[1], "count": r[2], "sample_subject": r[3]}
            for r in rows
        ]

    def bulk_resolve(
        self,
        *,
        sender_domain: str | None = None,
        sender_address: str | None = None,
        recipient_address: str | None = None,
        company_id: UUID,
        application_id: UUID | None = None,
        contact_id: UUID | None = None,
        by: str,
    ) -> int:
        """Propagate one learned, human-confirmed fact to every still-
        unclassified message it covers. Returns the row count.

        The three match args are OR'd: sender_domain/sender_address catch
        inbound mail *from* the correspondent, recipient_address also catches
        the user's own outbound replies *to* them — both directions of "the
        same people" (see migration 0007). At least one must be given, or
        nothing matches by construction and the call is a no-op.
        `classified_at IS NULL` scopes every propagation to the backlog only;
        an already-classified row (however it got that way) is never
        silently overwritten by a bulk rule.
        """
        matched = _fanout_predicate(
            sender_domain=sender_domain,
            sender_address=sender_address,
            recipient_address=recipient_address,
        )
        if matched is None:
            return 0
        where, params = matched
        cursor = self._conn.execute(
            "UPDATE message SET company_id = %s, application_id = %s,"
            " contact_id = %s, classified_at = now(), classified_by = %s"
            f" WHERE classified_at IS NULL AND ({where})",
            (company_id, application_id, contact_id, by, *params),
        )
        return cursor.rowcount

    def bulk_negative(
        self,
        *,
        sender_domain: str | None = None,
        sender_address: str | None = None,
        by: str,
    ) -> int:
        """The NEGATIVE half of `bulk_resolve` — a correspondent or domain
        confirmed to never be job-related (a newsletter platform's actual
        sending domain, say). No recipient_address form: the user's own
        outbound mail is never, on its own, evidence of what a correspondent
        is *not*."""
        matched = _fanout_predicate(
            sender_domain=sender_domain, sender_address=sender_address
        )
        if matched is None:
            return 0
        where, params = matched
        cursor = self._conn.execute(
            "UPDATE message SET classified_at = now(), classified_by = %s"
            f" WHERE classified_at IS NULL AND ({where})",
            (by, *params),
        )
        return cursor.rowcount

    def count_fanout(
        self,
        *,
        sender_domain: str | None = None,
        sender_address: str | None = None,
        recipient_address: str | None = None,
        backlog_only: bool = True,
    ) -> int:
        """How many rows share this attribute.

        With `backlog_only` (the default) this is exactly what
        `bulk_resolve`/`bulk_negative` would touch — the dashboard shows it
        before committing a rule, so a human can see "this resolves 4,412
        messages" and reconsider. It shares `_fanout_predicate` with both
        mutations rather than restating the match, because a preview that can
        disagree with the write it previews is worse than no preview at all.

        With `backlog_only=False` it counts every message carrying the
        attribute, classified or not. The teach form needs both numbers: once
        the mailbox has been fully swept the backlog count is 0 for every
        rule, and a lone "0" reads as a broken form rather than as "this rule
        is for mail that has not arrived yet".
        """
        matched = _fanout_predicate(
            sender_domain=sender_domain,
            sender_address=sender_address,
            recipient_address=recipient_address,
        )
        if matched is None:
            return 0
        where, params = matched
        scope = "classified_at IS NULL AND " if backlog_only else ""
        row = self._conn.execute(
            f"SELECT count(*) FROM message WHERE {scope}({where})",
            params,
        ).fetchone()
        return 0 if row is None else int(row[0])

    def mark_company_negative(self, company_id: UUID, by: str) -> int:
        """Label every message linked to this company — primary
        (`message.company_id`) or secondary (`message_company`) — negative,
        directly, at the email level. Returns the count touched.

        Not a reset to unclassified: `services/verify.py` already knows the
        verdict (it read every one of these messages to reach it), so the
        label is final the moment it's written, same as `mark_classified`
        everywhere else in the pipeline — no limbo state, no later resweep
        needed. Called immediately before `CompanyRepository.delete` removes
        the company row itself; a message reached only via the secondary
        link (this company was the agency, not the primary) is unlinked
        here too, since after this call there is no company left for either
        link to point at.
        """
        self._conn.execute(
            "DELETE FROM message_company WHERE company_id = %s"
            "   OR message_id IN (SELECT id FROM message WHERE company_id = %s)",
            (company_id, company_id),
        )
        cursor = self._conn.execute(
            "UPDATE message SET classified_at = now(), classified_by = %s,"
            " company_id = NULL, application_id = NULL"
            " WHERE company_id = %s",
            (by, company_id),
        )
        return cursor.rowcount

    def mark_classified(self, message_id: UUID, by: str) -> None:
        """Record that this message has been read, and by which extractor.

        The model name matters: when the numbers move, it is the only way to
        tell which rows came from which extractor (I3).
        """
        self._conn.execute(
            "UPDATE message SET classified_at = now(), classified_by = %s"
            " WHERE id = %s",
            (by, message_id),
        )

    def clear_classification(self, limit: int = 2000) -> int:
        """Reset one batch of already-classified messages. Returns the count
        actually touched (0 means nothing left to clear).

        What `jobd classify --reclassify` runs after a prompt/model/rule
        change. Two things that matter on a large table with small
        `shared_buffers` (this box): `WHERE classified_at IS NOT NULL` — a
        bare UPDATE with no WHERE rewrites every row including the ones
        already NULL, which on a 58k-row table meant paying for ~9x the
        writes actually needed; and LIMIT — one unconditional UPDATE over
        every classified row is one long transaction with no visible
        progress and no commit point if it's interrupted. The caller loops
        calling this (see cli.main's reclassify loop) and commits per batch,
        so a slow disk sees steady partial progress instead of a single
        multi-minute statement.
        """
        cursor = self._conn.execute(
            "UPDATE message SET classified_at = NULL, classified_by = NULL,"
            " company_id = NULL, application_id = NULL, contact_id = NULL"
            " WHERE id IN (SELECT id FROM message"
            "   WHERE classified_at IS NOT NULL LIMIT %s)",
            (limit,),
        )
        return cursor.rowcount

    def set_body(self, message_id: UUID, text: str) -> None:
        """Fill body_text. The generated tsvector follows automatically."""
        self._conn.execute(
            "UPDATE message SET body_text = %s WHERE id = %s", (text, message_id)
        )

    def link_thread_siblings(
        self, thread_id: str, *, company_id: UUID, application_id: UUID | None
    ) -> int:
        """Backward thread propagation: when one message in a thread earns a
        positive link, every still-unlinked sibling inherits it — the same
        thread-membership argument carry-forward already makes, applied to
        messages classified before the thread was settled. COALESCE-shaped
        like `link`; rows that already point somewhere are never moved (the
        audit owns corrections). Negatives are deliberately NOT propagated:
        a wrong negative would poison a whole real conversation, while a
        wrong positive is exactly what the audit tier exists to catch.
        Returns how many siblings were linked."""
        cursor = self._conn.execute(
            """
            UPDATE message SET
                company_id = %s,
                application_id = COALESCE(application_id, %s),
                classified_by = 'thread-reconcile'
            WHERE thread_id = %s AND company_id IS NULL
            """,
            (company_id, application_id, thread_id),
        )
        return cursor.rowcount or 0

    def rfc_parent_context(
        self, in_reply_to: str
    ) -> tuple[UUID, UUID | None, UUID | None, None] | None:
        """`thread_context`'s RFC fallback: resolve by the In-Reply-To header
        against stored Message-IDs (migration 0026) — the chain identity that
        exists on every mail source, native thread id or not. Same tuple
        shape as `thread_context` so callers treat the two uniformly; the
        agency slot is None because message_company isn't consulted here."""
        row = self._one(
            """
            SELECT company_id, application_id, contact_id FROM message
            WHERE message_id_header = %s AND company_id IS NOT NULL
            ORDER BY sent_at DESC LIMIT 1
            """,
            (in_reply_to,),
        )
        if row is None:
            return None
        return (row[0], row[1], row[2], None)

    def thread_latest(self, thread_id: str) -> dict[str, Any] | None:
        """The newest thread member with a stored body, plus the thread facts
        the renderer's header lines want — one query, for the one-call-per-
        thread extractor. Members without a non-empty derived body are
        skipped because classification walks oldest-first: on a fresh
        mailbox the newer siblings' bodies are not derived yet (ingest
        leaves body_text EMPTY, not NULL — live-caught: an IS NOT NULL
        guard let a bodiless sibling through and the thread's one reading
        was made from a subject line), and a header-only render would
        waste the one paid call the thread gets."""
        row = self._one(
            """
            SELECT m.id, m.sender_address, m.recipient_addresses, m.subject,
                   m.body_text, m.sent_at, m.reply_to, m.cc_addresses,
                   m.raw_metadata ->> 'labels',
                   (SELECT count(*) FROM message t WHERE t.thread_id = %s),
                   (SELECT string_agg(DISTINCT t.sender_address, ', ')
                    FROM (SELECT sender_address FROM message
                          WHERE thread_id = %s AND sender_address IS NOT NULL
                          LIMIT 6) t),
                   (SELECT min(t.sent_at) FROM message t WHERE t.thread_id = %s)
            FROM message m
            WHERE m.thread_id = %s AND nullif(m.body_text, '') IS NOT NULL
            ORDER BY m.sent_at DESC NULLS LAST LIMIT 1
            """,
            (thread_id, thread_id, thread_id, thread_id),
        )
        if row is None:
            return None
        return {
            "id": row[0],
            "sender": row[1] or "",
            "recipients": ", ".join(row[2] or []),
            "subject": row[3] or "",
            "body": row[4] or "",
            "sent_at": row[5],
            "reply_to": row[6],
            "cc": ", ".join(row[7] or []) if row[7] else None,
            "labels": row[8],
            "thread_messages": int(row[9] or 0),
            "thread_senders": row[10],
            "first_date": str(row[11]) if row[11] else None,
        }

    def thread_extraction(self, thread_id: str) -> dict[str, Any] | None:
        """The stored one-call-per-thread reading, if the model already made
        one — and only while it still covers the thread's newest evidence.
        A reading computed mid-ingest (or before a new reply arrived) is
        stale: reusing it would stamp the old verdict onto mail the model
        never saw, so a stale row reads as a miss and the caller's upsert
        replaces it. Returns the raw payload — `from_payload` re-derives
        the record view, so cache hits and fresh calls flow through
        identical code."""
        row = self._one(
            """
            SELECT te.payload, te.model FROM thread_extraction te
            WHERE te.thread_id = %s
              AND te.covers_sent_at >= coalesce(
                    (SELECT max(sent_at) FROM message WHERE thread_id = %s),
                    te.covers_sent_at)
            """,
            (thread_id, thread_id),
        )
        if row is None:
            return None
        return {"payload": row[0], "model": row[1]}

    def save_thread_extraction(
        self,
        thread_id: str,
        *,
        latest_message_id: UUID | None,
        covers_sent_at: Any,
        model: str,
        payload: dict[str, Any],
        messages_covered: int,
        reasoning: str | None = None,
    ) -> None:
        """Upsert: a later arrival with new evidence replaces the reading;
        anything at or before `covers_sent_at` reuses it."""
        from psycopg.types.json import Jsonb

        clients = payload.get("client_companies")
        self._conn.execute(
            """
            INSERT INTO thread_extraction
                (id, thread_id, latest_message_id, covers_sent_at, model,
                 label, company_name, company_domain, company_kind,
                 agency_name, agency_domain, role_title, stage, relationship,
                 client_companies, payload, messages_covered, reasoning)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (thread_id) DO UPDATE SET
                latest_message_id = EXCLUDED.latest_message_id,
                covers_sent_at = EXCLUDED.covers_sent_at,
                model = EXCLUDED.model,
                label = EXCLUDED.label,
                company_name = EXCLUDED.company_name,
                company_domain = EXCLUDED.company_domain,
                company_kind = EXCLUDED.company_kind,
                agency_name = EXCLUDED.agency_name,
                agency_domain = EXCLUDED.agency_domain,
                role_title = EXCLUDED.role_title,
                stage = EXCLUDED.stage,
                relationship = EXCLUDED.relationship,
                client_companies = EXCLUDED.client_companies,
                payload = EXCLUDED.payload,
                messages_covered = EXCLUDED.messages_covered,
                reasoning = coalesce(EXCLUDED.reasoning,
                                     thread_extraction.reasoning),
                updated_at = now()
            """,
            (
                thread_id,
                latest_message_id,
                covers_sent_at,
                model,
                str(payload.get("label") or "unclassified"),
                payload.get("company_name"),
                payload.get("company_domain"),
                payload.get("company_kind"),
                payload.get("agency_name"),
                payload.get("agency_domain"),
                payload.get("role_title"),
                payload.get("stage"),
                payload.get("relationship"),
                Jsonb(clients if isinstance(clients, list) else []),
                Jsonb(payload),
                messages_covered,
                reasoning,
            ),
        )

    def link(
        self,
        message_id: UUID,
        *,
        company_id: UUID | None = None,
        application_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> None:
        """Attach a message to the record. COALESCE, so a later pass with less
        information cannot unlink what an earlier one established."""
        self._conn.execute(
            "UPDATE message SET"
            "   company_id = COALESCE(%s, company_id),"
            "   application_id = COALESCE(%s, application_id),"
            "   contact_id = COALESCE(%s, contact_id)"
            " WHERE id = %s",
            (company_id, application_id, contact_id, message_id),
        )

    def set_embedding(self, message_id: UUID, vector: list[float]) -> None:
        """Store a semantic embedding. Width must match the column (see
        docs/schema.md — a different embedder is a migration)."""
        literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
        self._conn.execute(
            "UPDATE message SET embedding = %s::vector WHERE id = %s",
            (literal, message_id),
        )

    def search(self, query: str, limit: int = 50) -> list[Message]:
        """Full-text search over subject and body.

        Runs in SQL against the generated tsvector, not in Python. P4.1 requires
        that of the dashboard filters; starting anywhere else here would mean
        writing it twice.
        """
        rows = self._conn.execute(
            f"SELECT {_MESSAGE_COLS} FROM message"
            " WHERE search_tsv @@ websearch_to_tsquery('english', %s)"
            " ORDER BY ts_rank(search_tsv, websearch_to_tsquery('english', %s)) DESC,"
            " sent_at DESC LIMIT %s",
            (query, query, limit),
        ).fetchall()
        return [_message(r) for r in rows]


class StageEventRepository(Repository):
    """Stage transitions, each pointing at the message that evidences it."""

    def add(self, event: StageEvent) -> StageEvent:
        """Add, or refresh, the stage this evidence supports.

        ON CONFLICT rather than a plain INSERT: `stage_event_evidence_key`
        (application_id, stage, evidence_message_id) is unique by design (the
        same evidence must not produce the same stage twice, I2) — but a
        reclassify (`clear_classification` + rerun, e.g. after a model swap)
        re-derives the same evidence, and re-deriving the same fact is not an
        error, it is the common case. Without the upsert, a reclassify would
        hit this constraint on almost every previously-recorded stage event
        and roll back the whole message as an error rather than confirm it —
        exactly the "append, not truncate" architecture, not fought against
        it.
        """
        new_id = self._id(
            "INSERT INTO stage_event (application_id, stage, occurred_at,"
            " evidence_message_id, confidence, extracted_by)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (application_id, stage, evidence_message_id) DO UPDATE SET"
            "   occurred_at = EXCLUDED.occurred_at,"
            "   confidence = EXCLUDED.confidence,"
            "   extracted_by = EXCLUDED.extracted_by"
            " RETURNING id",
            (
                event.application_id,
                event.stage,
                event.occurred_at,
                event.evidence_message_id,
                event.confidence,
                event.extracted_by,
            ),
        )
        return StageEvent(**{**_fields(event), "id": new_id})

    def for_application(self, application_id: UUID) -> list[StageEvent]:
        """Chronological, because a timeline read out of order is not a timeline."""
        rows = self._conn.execute(
            "SELECT id, application_id, stage, occurred_at, evidence_message_id,"
            " confidence, extracted_by FROM stage_event"
            " WHERE application_id = %s ORDER BY occurred_at",
            (application_id,),
        ).fetchall()
        return [
            StageEvent(
                id=UUID(str(r[0])),
                application_id=UUID(str(r[1])),
                stage=r[2],
                occurred_at=r[3],
                evidence_message_id=UUID(str(r[4])),
                confidence=None if r[5] is None else float(r[5]),
                extracted_by=r[6],
            )
            for r in rows
        ]

    def iter_all(self) -> Iterator[UUID]:
        for row in self._conn.execute("SELECT id FROM stage_event").fetchall():
            yield UUID(str(row[0]))


# --------------------------------------------------------------------- helpers

_MESSAGE_COLS = (
    "id, storage_key, channel, account, external_id, thread_id, sender_address,"
    " sender_domain, recipient_addresses, direction, sent_at, subject,"
    " body_text, company_id, application_id, contact_id"
)


def _fields(obj: Any) -> dict[str, Any]:
    """Shallow field dict for a slotted frozen dataclass."""
    return {name: getattr(obj, name) for name in obj.__slots__}


def _uuid_or_none(value: Any) -> UUID | None:
    return None if value is None else UUID(str(value))


def _company(row: tuple[Any, ...]) -> Company:
    return Company(
        id=UUID(str(row[0])),
        canonical_name=row[1],
        domain=row[2],
        first_seen_at=row[3],
        last_seen_at=row[4],
        kind=row[5],
    )


def _application(row: tuple[Any, ...]) -> Application:
    return Application(
        id=UUID(str(row[0])),
        company_id=UUID(str(row[1])),
        role_title=row[2],
        started_at=row[3],
        ended_at=row[4],
        outcome=row[5],
        source=row[6],
    )


def _message(row: tuple[Any, ...]) -> Message:
    return Message(
        id=UUID(str(row[0])),
        storage_key=row[1],
        channel=row[2],
        account=row[3],
        external_id=row[4],
        thread_id=row[5],
        sender_address=row[6],
        sender_domain=row[7],
        recipient_addresses=tuple(row[8] or ()),
        direction=row[9],
        sent_at=row[10],
        subject=row[11],
        body_text=row[12],
        company_id=_uuid_or_none(row[13]),
        application_id=_uuid_or_none(row[14]),
        contact_id=_uuid_or_none(row[15]),
    )


class ReviewQueueRepository(Repository):
    """Extractions the model was not sure enough about (P2).

    Nothing here has touched the record. That is the contract: a pending item
    means the system noticed something and declined to act on it.
    """

    def enqueue(
        self,
        message_id: UUID,
        *,
        extraction: dict[str, Any],
        reason: str,
        extracted_by: str,
        confidence: float | None = None,
    ) -> UUID:
        """Add or refresh the open item for a message.

        ON CONFLICT rather than a second row: re-running the classifier must
        update the existing doubt, not stack another copy of it (I2).
        """
        row = self._one(
            "INSERT INTO review_queue"
            " (message_id, extraction, confidence, reason, extracted_by)"
            " VALUES (%s, %s::jsonb, %s, %s, %s)"
            " ON CONFLICT (message_id) WHERE status = 'pending' DO UPDATE SET"
            "   extraction = EXCLUDED.extraction,"
            "   confidence = EXCLUDED.confidence,"
            "   reason = EXCLUDED.reason,"
            "   extracted_by = EXCLUDED.extracted_by,"
            "   created_at = now()"
            " RETURNING id",
            (message_id, json.dumps(extraction), confidence, reason, extracted_by),
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise RuntimeError("INSERT ... RETURNING produced no row")
        return UUID(str(row[0]))

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Open items, oldest first."""
        rows = self._conn.execute(
            "SELECT id, message_id, extraction, confidence, reason, extracted_by,"
            " created_at FROM review_queue WHERE status = 'pending'"
            " ORDER BY created_at LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            {
                "id": UUID(str(r[0])),
                "message_id": UUID(str(r[1])),
                "extraction": r[2],
                "confidence": None if r[3] is None else float(r[3]),
                "reason": r[4],
                "extracted_by": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def count_pending(self) -> int:
        row = self._one(
            "SELECT count(*) FROM review_queue WHERE status = %s", ("pending",)
        )
        return 0 if row is None else int(row[0])

    def resolve(self, review_id: UUID, status: str) -> None:
        """Close an item. `approved` or `rejected`; both need a human."""
        if status not in {"approved", "rejected"}:
            raise ValueError(f"status must be approved or rejected, got {status!r}")
        self._conn.execute(
            "UPDATE review_queue SET status = %s, resolved_at = now() WHERE id = %s",
            (status, review_id),
        )

    def resolve_by_message(self, message_id: UUID) -> None:
        """Delete any pending item for a message that got resolved another
        way — thread-carry, or a fresh classify pass that now records or
        drops it. A plain DELETE, not a status change: nobody decided
        anything here for it to be worth remembering, and it would
        otherwise linger with extraction/reason text that no longer
        matches reality. A no-op when there is no pending row.
        """
        self._conn.execute(
            "DELETE FROM review_queue WHERE message_id = %s AND status = 'pending'",
            (message_id,),
        )


class SenderRuleRepository(Repository):
    """Learned domain/address rules (fanout classification, migration 0008).

    The rule set is small by construction — dozens to low hundreds of
    correspondents and platforms, not one per message — so callers load it
    once with :meth:`all` and match against it in memory for a whole batch,
    rather than querying per message.
    """

    def add(self, rule: SenderRule) -> SenderRule:
        """Learn one rule, or update it if this attribute was already ruled on.

        ON CONFLICT DO UPDATE, not a plain INSERT: correcting an earlier
        wrong call ("actually this domain is positive, not negative") must
        overwrite, not sit behind a uniqueness error.
        """
        row = self._one(
            "INSERT INTO sender_rule (match_type, value, verdict, category,"
            " company_id, source) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (match_type, lower(value)) DO UPDATE SET"
            "   verdict = EXCLUDED.verdict, category = EXCLUDED.category,"
            "   company_id = EXCLUDED.company_id, source = EXCLUDED.source"
            " RETURNING id",
            (
                rule.match_type, rule.value, rule.verdict, rule.category,
                rule.company_id, rule.source,
            ),
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise RuntimeError("INSERT ... RETURNING produced no row")
        return SenderRule(**{**_fields(rule), "id": UUID(str(row[0]))})

    def all(self) -> list[SenderRule]:
        """Every rule, with a categorised rule's verdict/company resolved
        from `sender_category` — the group's row wins over the rule's own
        (mostly-null) columns, so re-deciding a whole category later needs no
        change here."""
        rows = self._conn.execute(
            "SELECT sr.id, sr.match_type, sr.value,"
            "  COALESCE(sc.verdict, sr.verdict) AS verdict,"
            "  sr.category,"
            "  COALESCE(sc.company_id, sr.company_id) AS company_id,"
            "  sr.source"
            " FROM sender_rule sr"
            " LEFT JOIN sender_category sc ON sc.category = sr.category"
        ).fetchall()
        return [
            SenderRule(
                id=UUID(str(r[0])),
                match_type=r[1],
                value=r[2],
                verdict=r[3],
                category=r[4],
                company_id=_uuid_or_none(r[5]),
                source=r[6],
            )
            for r in rows
        ]


class MessageCompanyRepository(Repository):
    """The secondary company/agency link on a message (migration 0013).

    Additive only — `MessageRepository.link`'s `company_id` is the primary
    link and is never touched here. This exists for the one case a single
    FK can't represent: a message that names both a recruiting agency and
    the specific client company it concerns.
    """

    def link(self, message_id: UUID, company_id: UUID, *, role: str) -> None:
        """Idempotent: a re-run over an already-linked message (a
        `--reclassify`, a thread-carry re-derivation) must not error or
        duplicate the row (I2)."""
        self._conn.execute(
            "INSERT INTO message_company (message_id, company_id, role)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (message_id, company_id) DO UPDATE SET role = EXCLUDED.role",
            (message_id, company_id, role),
        )

    def for_company(self, company_id: UUID, role: str = "agency") -> list[UUID]:
        """Every message secondarily linked to this company under `role` —
        e.g. every message an agency (kind='agency') sourced, even the ones
        whose primary `company_id` points at the actual employer instead."""
        rows = self._conn.execute(
            "SELECT message_id FROM message_company"
            " WHERE company_id = %s AND role = %s",
            (company_id, role),
        ).fetchall()
        return [UUID(str(r[0])) for r in rows]


class CompanyVerificationRepository(Repository):
    """Findings from `services/verify.py`'s independent whole-chain audit
    pass (migration 0015). Insert-only — a re-verify writes a new row rather
    than overwriting the last one, so whether an earlier pass got something
    wrong stays visible instead of being silently replaced.
    """

    def add(self, verification: CompanyVerification) -> CompanyVerification:
        new_id = self._id(
            "INSERT INTO company_verification (company_id, model, message_count,"
            " classification_correct, verified_name, verified_kind,"
            " verified_domain, reasoning, suggested_rules, raw_response)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)"
            " RETURNING id",
            (
                verification.company_id,
                verification.model,
                verification.message_count,
                verification.classification_correct,
                verification.verified_name,
                verification.verified_kind,
                verification.verified_domain,
                verification.reasoning,
                json.dumps(verification.suggested_rules),
                json.dumps(verification.raw_response),
            ),
        )
        return CompanyVerification(**{**_fields(verification), "id": new_id})

    def latest_for_company(self, company_id: UUID) -> CompanyVerification | None:
        """Most recent pass, or None if this company has never been
        verified — what the dashboard shows on a company page."""
        row = self._one(
            "SELECT id, company_id, model, message_count, classification_correct,"
            " verified_name, verified_kind, verified_domain, reasoning,"
            " suggested_rules, raw_response, created_at"
            " FROM company_verification WHERE company_id = %s"
            " ORDER BY created_at DESC LIMIT 1",
            (company_id,),
        )
        return None if row is None else _verification(row)

    def verified_company_ids(self) -> set[UUID]:
        """Every company with at least one prior pass — what `services.verify`
        uses to skip already-verified companies unless `reverify` is asked
        for, so a sweep over the backlog makes progress instead of paying to
        re-read the same chains every run."""
        rows = self._conn.execute(
            "SELECT DISTINCT company_id FROM company_verification"
            " WHERE company_id IS NOT NULL"
        ).fetchall()
        return {UUID(str(r[0])) for r in rows}


def _verification(row: tuple[Any, ...]) -> CompanyVerification:
    return CompanyVerification(
        id=UUID(str(row[0])),
        company_id=_uuid_or_none(row[1]),
        model=row[2],
        message_count=int(row[3]),
        classification_correct=row[4],
        verified_name=row[5],
        verified_kind=row[6],
        verified_domain=row[7],
        reasoning=row[8],
        suggested_rules=row[9] or [],
        raw_response=row[10] or {},
        created_at=row[11],
    )


class PromptSnippetRepository(Repository):
    """A small, human-curated library of reusable instruction text
    (migration 0022) — labelled text a human saved once to reuse wherever a
    free-text "extra instructions" field feeds a model call."""

    def all(self) -> list[PromptSnippet]:
        rows = self._conn.execute(
            "SELECT id, label, text, created_at FROM prompt_snippet"
            " ORDER BY created_at"
        ).fetchall()
        return [_prompt_snippet(r) for r in rows]

    def add(self, label: str, text: str) -> PromptSnippet:
        new_id = self._id(
            "INSERT INTO prompt_snippet (label, text) VALUES (%s, %s) RETURNING id",
            (label, text),
        )
        return PromptSnippet(id=new_id, label=label, text=text)

    def delete(self, snippet_id: UUID) -> None:
        self._conn.execute("DELETE FROM prompt_snippet WHERE id = %s", (snippet_id,))


def _prompt_snippet(row: tuple[Any, ...]) -> PromptSnippet:
    return PromptSnippet(
        id=UUID(str(row[0])), label=row[1], text=row[2], created_at=row[3]
    )


class SenderCategoryRepository(Repository):
    """One verdict per named group (`SenderCategory`, migration 0009)."""

    def add(
        self,
        category: str,
        verdict: Literal["positive", "negative", "undecided"],
        company_id: UUID | None = None,
    ) -> None:
        """Set (or change) a whole category's verdict in one place.

        ON CONFLICT DO UPDATE: this is exactly the "change once" case the
        category concept exists for — re-deciding a group must update every
        rule tagged with it, and a join at read time (`SenderRuleRepository
        .all`) is what makes that true without touching those rows.
        """
        self._conn.execute(
            "INSERT INTO sender_category (category, verdict, company_id)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (category) DO UPDATE SET"
            "   verdict = EXCLUDED.verdict, company_id = EXCLUDED.company_id",
            (category, verdict, company_id),
        )

    def verdict_for(
        self, category: str
    ) -> Literal["positive", "negative", "undecided"] | None:
        row = self._one(
            "SELECT verdict FROM sender_category WHERE category = %s", (category,)
        )
        return None if row is None else row[0]
