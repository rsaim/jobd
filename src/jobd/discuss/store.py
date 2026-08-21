"""Persistence for discussion transcripts. One table, created on first use.

Not a numbered migration on purpose: the discussion is an instrument, not
part of the record — `jobd rebuild` must never think it owes this table
anything. CREATE IF NOT EXISTS keeps it reproducible on any checkout.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

_DDL = """
CREATE TABLE IF NOT EXISTS discussion_message (
    id bigserial PRIMARY KEY,
    run_id text NOT NULL,
    seq int NOT NULL,
    round int NOT NULL,
    model text NOT NULL,
    display text NOT NULL,
    role text NOT NULL DEFAULT 'panelist',
    content text NOT NULL,
    meta jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
)
"""


def connect() -> psycopg.Connection[Any]:
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    conn.execute(_DDL)
    conn.commit()
    return conn


def add(
    conn: psycopg.Connection[Any],
    *,
    run_id: str,
    seq: int,
    round_no: int,
    model: str,
    display: str,
    content: str,
    role: str = "panelist",
    meta: dict[str, Any] | None = None,
) -> None:
    from psycopg.types.json import Jsonb

    conn.execute(
        """
        INSERT INTO discussion_message
            (run_id, seq, round, model, display, role, content, meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, seq) DO NOTHING
        """,
        (run_id, seq, round_no, model, display, role, content,
         Jsonb(meta) if meta else None),
    )
    conn.commit()


def transcript(conn: psycopg.Connection[Any], run_id: str | None = None) -> list[dict[str, Any]]:
    """Full transcript, oldest first. No run_id: the latest run."""
    with conn.cursor(row_factory=dict_row) as cur:
        if run_id is None:
            row = cur.execute(
                "SELECT run_id FROM discussion_message"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return []
            run_id = row["run_id"]
        return cur.execute(
            "SELECT * FROM discussion_message WHERE run_id = %s ORDER BY seq",
            (run_id,),
        ).fetchall()


def runs(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            """
            SELECT run_id, min(created_at) AS started,
                   count(*) AS turns, count(DISTINCT model) AS voices
            FROM discussion_message GROUP BY run_id ORDER BY started DESC
            """
        ).fetchall()
