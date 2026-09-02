"""Re-derive every application's stage timeline from its whole mail chain.

The counterpart to `domain.timeline_extraction`: one model call per
application, over the ordered chain, replacing the per-message `stage` guesses
that produced 833 incoherent events across 229 applications.

Idempotent by construction (I2). Each application's derived events are written
with `extracted_by='timeline'`, and a re-run deletes that application's
previous `timeline` rows before writing the new ones -- so running twice is
the same as running once. Rows from other paths (`llm`, `manual`) are left
alone: a human's stage event is not the machine's to discard.

Nothing here re-reads message bodies. The chain is subjects, dates, senders
and direction (see `render_chain`), so this pass sends strictly less content
to the provider than the per-message extraction it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from jobd.domain.record import StageEvent, enforce_forward_order
from jobd.domain.timeline_extraction import (
    TIMELINE_MAX_TOKENS,
    TIMELINE_PROMPT,
    TIMELINE_SCHEMA,
    render_chain,
)


@dataclass
class DeriveResult:
    applications: int = 0
    llm_calls: int = 0
    events_written: int = 0
    events_replaced: int = 0
    unrelated: int = 0
    skipped_empty: int = 0
    bad_evidence_index: int = 0
    events_dropped_backwards: int = 0
    errors: list[str] = field(default_factory=list)


def _chain(conn: Any, application_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, sent_at, direction, sender_address, subject, body_text FROM message"
        " WHERE application_id = %s ORDER BY sent_at, id",
        (application_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "sent_at": r[1],
            "direction": r[2],
            "sender_address": r[3],
            "subject": r[4],
            "body_text": r[5],
        }
        for r in rows
    ]


def derive_stages(
    conn: Any,
    llm: Any,
    *,
    limit: int | None = None,
    application_id: UUID | None = None,
    apply: bool = False,
) -> DeriveResult:
    """Derive timelines for applications that have mail. `apply` writes.

    Dry by default: a stage rewrite is destructive enough that seeing the
    result before committing to it is the sane order, same discipline as
    `jobd distill`'s `--apply`.
    """
    out = DeriveResult()
    if application_id is not None:
        targets = [application_id]
    else:
        rows = conn.execute(
            "SELECT a.id FROM application a"
            " JOIN message m ON m.application_id = a.id"
            " GROUP BY a.id HAVING count(m.id) > 0"
            " ORDER BY count(m.id) DESC" + (" LIMIT %s" if limit else ""),
            (limit,) if limit else (),
        ).fetchall()
        targets = [r[0] for r in rows]

    for app_id in targets:
        messages = _chain(conn, app_id)
        if not messages:
            out.skipped_empty += 1
            continue
        out.applications += 1
        try:
            payload = llm.extract(
                TIMELINE_PROMPT, TIMELINE_SCHEMA, text=render_chain(messages)
            )
            out.llm_calls += 1
        except Exception as exc:  # noqa: BLE001 — one bad chain must not stop the run
            out.errors.append(f"{app_id}: {type(exc).__name__}: {exc}")
            continue

        if not payload.get("related"):
            out.unrelated += 1
            continue

        events = []
        for item in payload.get("events") or []:
            idx = item.get("evidence_index")
            if not isinstance(idx, int) or not 1 <= idx <= len(messages):
                # The model cited a message that is not in the chain. Drop the
                # claim rather than guess at one: G2 promises every stage
                # links to real evidence, and an invented index is not that.
                out.bad_evidence_index += 1
                continue
            proof = messages[idx - 1]
            events.append((item["stage"], proof["sent_at"], proof["id"]))

        # Ordering is a fixed property of the vocabulary, so it is enforced
        # here rather than asked of the model: the timeline call reads the
        # chain well but still occasionally walks back down the funnel (a
        # "quick sync" after a coding round genuinely reads like a screen).
        ordered = enforce_forward_order(
            [
                StageEvent(
                    application_id=app_id,
                    stage=stage,  # type: ignore[arg-type]
                    occurred_at=occurred_at,
                    evidence_message_id=evidence_id,
                    extracted_by="timeline",
                )
                for stage, occurred_at, evidence_id in events
            ]
        )
        out.events_dropped_backwards += len(events) - len(ordered)

        if not apply:
            out.events_written += len(ordered)
            continue

        removed = conn.execute(
            "DELETE FROM stage_event WHERE application_id = %s"
            " AND extracted_by = 'timeline'",
            (app_id,),
        ).rowcount
        out.events_replaced += removed or 0
        for event in ordered:
            conn.execute(
                "INSERT INTO stage_event (application_id, stage, occurred_at,"
                " evidence_message_id, extracted_by)"
                " VALUES (%s, %s, %s, %s, 'timeline')"
                " ON CONFLICT (application_id, stage, evidence_message_id)"
                " DO UPDATE SET occurred_at = EXCLUDED.occurred_at,"
                "               extracted_by = EXCLUDED.extracted_by",
                (
                    app_id,
                    event.stage,
                    event.occurred_at,
                    event.evidence_message_id,
                ),
            )
            out.events_written += 1
        conn.commit()

    return out
