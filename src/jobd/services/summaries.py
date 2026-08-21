"""Per-company/agency mail summary — streamed, cached (M9 §5 applied to
company scope and to plain-markdown output; see domain/summary.py for why
it's no longer structured JSON).

Generation is a service the app calls on request (a page load, a button),
never a tool the chat model calls — the same split docs/chat-and-summaries.md
§2 draws between the chat's read-only connection and this one, which is a
normal read-write connection used only to write the cache row, on a request
path the model has no access to.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import UUID

import psycopg

from jobd.domain.summary import PROMPT, render_messages
from jobd.ports import LLMProvider
from jobd.ports.chat import Budget, ChatEvent, Done, Turn
from jobd.services import dashboard

#: Bodies are already quote/footer-stripped (envelope.body_text), so this is
#: generous without being reckless — a real thread's own content, not the
#: repeated history beneath it. Oldest-first, capped, so a very long
#: correspondence is read as "how it started and progressed" rather than
#: cut off mid-story from the wrong end.
_MAX_MESSAGES = 60
_MAX_BODY_CHARS = 2_000


def _fingerprint(conn: psycopg.Connection[Any], company_id: UUID) -> str:
    """Sha256 over the facts the summary derives from: message count, latest
    message, latest stage event. Regenerate only when one of these moves —
    the whole point of a cache."""
    row = conn.execute(
        """
        SELECT count(m.id), max(m.sent_at), max(se.occurred_at)
        FROM message m
        LEFT JOIN application a ON a.id = m.application_id
        LEFT JOIN stage_event se ON se.application_id = a.id
        WHERE m.company_id = %(company_id)s
        """,
        {"company_id": company_id},
    ).fetchone()
    basis = f"{company_id}:{row[0]}:{row[1]}:{row[2]}" if row else str(company_id)
    return hashlib.sha256(basis.encode()).hexdigest()


def cached_summary(
    conn: psycopg.Connection[Any], company_id: UUID
) -> dict[str, Any] | None:
    """The cached row if one exists, regardless of freshness — the caller
    decides whether stale is good enough to show while a regenerate runs."""
    row = conn.execute(
        "SELECT summary, model, fingerprint, created_at"
        " FROM summary_cache WHERE scope = 'company' AND scope_id = %(id)s",
        {"id": company_id},
    ).fetchone()
    if row is None:
        return None
    summary, model, fingerprint, created_at = row
    return {
        "markdown": summary.get("markdown", ""),
        "model": model,
        "fingerprint": fingerprint,
        "created_at": created_at,
        "current_fingerprint": _fingerprint(conn, company_id),
    }


def _digest(conn: psycopg.Connection[Any], company_id: UUID) -> str | None:
    """The rendered <message> block this company's summary is grounded in,
    or None when there is nothing to summarize (M7 gate 2: reuses
    dashboard.list_communications, not a second query over the same table)."""
    rows = dashboard.list_communications(
        conn, company_id=company_id, limit=_MAX_MESSAGES, order="asc"
    )
    if not rows:
        return None
    messages = [
        {
            "id": str(r.id),
            "direction": r.direction,
            "sent_at": r.sent_at.isoformat(),
            "subject": r.subject,
            "body_text": (r.body_text or "")[:_MAX_BODY_CHARS],
        }
        for r in rows
    ]
    return render_messages(messages)


def stream_company_summary(
    conn: psycopg.Connection[Any],
    company_id: UUID,
    provider: LLMProvider,
) -> Iterator[ChatEvent]:
    """Stream a fresh summary, writing the accumulated text into the cache
    once the model finishes — the same fingerprinted, single-caller-writes
    cache row `cached_summary` reads, just filled by a streaming call
    instead of a blocking one.

    Single-flight via `pg_try_advisory_lock`, held for the generator's whole
    lifetime (a `finally` around the stream, not just the check) — the
    thing worth guarding against here is a page-load auto-trigger racing an
    identical one from a second open tab, not usually a human clicking
    twice, but the guard is the same shape either way: lose the race, get
    nothing (`Done("busy")`), the caller falls back to whatever is cached.

    Yields `ChatEvent`s verbatim (the same union `/api/chat` already streams
    as SSE) so `web/api.py` can serve this exactly like a chat turn — one
    fewer wire format for the frontend to know about.
    """
    digest = _digest(conn, company_id)
    if digest is None:
        yield Done("stop")
        return

    key = f"summary:company:{company_id}"
    got_lock = conn.execute(
        "SELECT pg_try_advisory_lock(hashtext(%(key)s))", {"key": key}
    ).fetchone()
    if not got_lock or not got_lock[0]:
        yield Done("busy")
        return

    text = ""
    try:
        converse = getattr(provider, "converse", None)
        if converse is None:
            yield Done("error")
            return
        for event in converse(
            [Turn(role="user", text=digest)],
            [],
            budget=Budget(),
            system_prompt=PROMPT,
        ):
            if hasattr(event, "text"):
                text += event.text  # type: ignore[attr-defined]
            yield event
            if isinstance(event, Done):
                break
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%(key)s))", {"key": key})

    if not text.strip():
        return

    fingerprint = _fingerprint(conn, company_id)
    conn.execute(
        """
        INSERT INTO summary_cache (scope, scope_id, fingerprint, model, summary, citations)
        VALUES ('company', %(company_id)s, %(fingerprint)s, %(model)s, %(summary)s::jsonb, '[]'::jsonb)
        ON CONFLICT (scope, scope_id) WHERE scope_id IS NOT NULL
        DO UPDATE SET fingerprint = EXCLUDED.fingerprint,
                      model       = EXCLUDED.model,
                      summary     = EXCLUDED.summary,
                      created_at  = now()
        """,
        {
            "company_id": company_id,
            "fingerprint": fingerprint,
            "model": provider.name,
            "summary": _to_json({"markdown": text}),
        },
    )
    conn.commit()


def _to_json(value: Any) -> str:
    import json

    return json.dumps(value)
