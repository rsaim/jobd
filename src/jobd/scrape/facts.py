"""Interesting facts for the live dashboard ticker.

Small SQL reads over the record, phrased as one-line human sentences. Each
helper is cheap enough to run at node boundaries mid-scrape — the dashboard
shows them as they land, which is what keeps a multi-minute run watchable.
Nothing here is load-bearing: a fact query that returns nothing yields no
fact, never an error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg


def new_company_facts(
    conn: psycopg.Connection[Any], since: datetime, limit: int = 8
) -> list[str]:
    """Companies the record gained during this run, newest first."""
    rows = conn.execute(
        """
        SELECT canonical_name, kind, domain
        FROM company
        WHERE created_at >= %(since)s
        ORDER BY created_at DESC
        LIMIT %(limit)s
        """,
        {"since": since, "limit": limit},
    ).fetchall()
    out = []
    for name, kind, domain in rows:
        what = "recruiting agency" if kind == "agency" else "company"
        tail = f" ({domain})" if domain else ""
        out.append(f"New {what}: {name}{tail}")
    return out


def stage_facts(conn: psycopg.Connection[Any], since: datetime) -> list[str]:
    """Pipeline movement discovered this run — offers and rejections lead."""
    rows = conn.execute(
        """
        SELECT s.stage, c.canonical_name, count(*)
        FROM stage_event s
        JOIN application a ON a.id = s.application_id
        JOIN company c ON c.id = a.company_id
        WHERE s.created_at >= %(since)s
        GROUP BY s.stage, c.canonical_name
        ORDER BY min(s.created_at) DESC
        LIMIT 12
        """,
        {"since": since},
    ).fetchall()
    verb = {
        "offer": "An offer from",
        "rejected": "A rejection from",
        "onsite": "An onsite with",
        "technical": "A technical round with",
        "phone_screen": "A phone screen with",
        "recruiter_screen": "A recruiter screen with",
        "applied": "An application to",
    }
    return [
        f"{verb.get(stage, f'A {stage} event at')} {name}"
        for stage, name, _n in rows
        if stage in verb
    ]


def mailbox_facts(
    conn: psycopg.Connection[Any], counters: dict[str, float]
) -> list[str]:
    """Closing numbers worth saying out loud."""
    facts: list[str] = []
    row = conn.execute(
        """
        SELECT count(DISTINCT c.id) FILTER (WHERE c.kind = 'employer'),
               count(DISTINCT c.id) FILTER (WHERE c.kind = 'agency'),
               (SELECT count(*) FROM application),
               (SELECT count(*) FROM stage_event WHERE stage = 'offer'),
               (SELECT count(*) FROM stage_event WHERE stage = 'rejected')
        FROM company c
        """
    ).fetchone()
    if row:
        employers, agencies, applications, offers, rejections = row
        facts.append(
            f"The record now holds {employers} employers, {agencies} agencies, "
            f"{applications} applications"
        )
        if offers or rejections:
            facts.append(f"All time: {offers} offers, {rejections} rejections")
    fetched = int(counters.get("fetched", 0))
    skipped = int(counters.get("listed_known", 0))
    if skipped:
        facts.append(
            f"Idempotency skipped {skipped:,} already-ingested messages before "
            f"any fetch was paid for"
        )
    free = int(
        sum(
            counters.get(k, 0)
            for k in ("filtered_out", "deterministic_calls", "carried_forward")
        )
    )
    if free and fetched:
        facts.append(
            f"{free:,} of {fetched:,} fetched messages were resolved without a "
            f"paid model call"
        )
    return facts
