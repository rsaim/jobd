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

import re

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from jobd.domain.record import (
    StageEvent,
    enforce_forward_order,
    onboarding_accepted_index,
)
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
    onboarding_accepted: int = 0
    #: True when the derived timeline reached an offer or a post-offer
    #: outcome. The record's headline fact per application, and what the
    #: offer eval scores against the operator's own account of which offers
    #: were real.
    offer_signal: bool = False
    errors: list[str] = field(default_factory=list)


def _chain(conn: Any, application_id: UUID) -> list[dict[str, Any]]:
    """This application's mail, plus its company's unattached mail.

    Messages that resolved to a company but to no application are included
    when the company has a single application, and they are exactly the ones
    that matter most here: an accepted offer is followed by onboarding,
    payroll and (in the live corpus) two months of immigration-attorney
    threads, none of which look like an application to the linker, so they
    end up company-linked and application-less. 149 of 2,487 company-linked
    messages sit in that state.

    Dropping them cost a real answer: a "Welcome to <company>" thread was the
    only evidence that one offer was ever extended -- the offer itself was
    conveyed outside email -- and the derived timeline stopped at the
    technical screen. The single-application guard is what keeps this honest:
    with two or more applications at a company there is no way to tell which
    one the loose mail belongs to, so it is left out rather than guessed at.
    """
    rows = conn.execute(
        """
        WITH me AS (SELECT company_id FROM application WHERE id = %(app)s),
        -- The company's applications that actually carry a conversation. A
        -- one-message row is usually a stray (post-offer mail spawning a
        -- role-less "application" -- the same artefact the app audit merges
        -- away), and letting it count would strand the loose mail nobody can
        -- claim. "Sole substantive application" is the honest test of
        -- ownership, not "sole row".
        substantive AS (
            SELECT a.id FROM application a JOIN me ON a.company_id = me.company_id
            WHERE (SELECT count(*) FROM message m2 WHERE m2.application_id = a.id) > 1
        )
        SELECT id, sent_at, direction, sender_address, subject, body_text
        FROM message
        WHERE application_id = %(app)s
           OR (application_id IS NULL
               AND company_id = (SELECT company_id FROM me)
               AND (SELECT count(*) FROM substantive) = 1
               AND %(app)s IN (SELECT id FROM substantive))
        ORDER BY sent_at, id
        """,
        {"app": application_id},
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


#: Stages that assert the candidate consented to an outcome. Only these are
#: vulnerable to a calendar RSVP being read as a hiring decision -- an
#: "Invitation:" that the model reads as an interview is right to keep.
_TERMINAL_BY_CONSENT = frozenset({"accepted", "declined"})

#: A meeting RSVP, which Google Calendar and Outlook prefix onto the invite
#: subject. Anchored: a subject that merely contains the word ("Offer
#: Accepted - please countersign") is a real acceptance and must pass.
_CALENDAR_RSVP = re.compile(r"(accepted|declined|tentative):\s", re.I)


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
    # One stamp for the whole run, so every application derived together
    # shares a batch and "the newest batch" is unambiguous per application.
    batch_at = conn.execute("SELECT now()").fetchone()[0]
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
            # A failed call is not a reason to discard evidence the free
            # deterministic rule can still read. Onboarding mail proves the
            # offer was accepted whether or not the model answered, and
            # dropping the whole application loses that outright — live-caught
            # against the operator's own offer list, where a truncated JSON
            # response ("Unterminated string") was the *only* reason a real
            # accepted offer went unrecorded. Degrade to the free tier
            # instead: fewer stages than a good call would give, never a
            # silently missing outcome.
            payload = None

        if not isinstance(payload, dict):
            # Strict json_schema mode still occasionally yields the wrong
            # top-level shape -- a bare list of events instead of the object
            # that wraps them. Treat it as a failed call rather than crashing
            # the run: the deterministic onboarding rule below still reads the
            # chain, so an accepted offer survives a malformed response.
            if payload is not None:
                out.errors.append(f"{app_id}: payload was {type(payload).__name__}")
            payload = None

        if payload is not None and not payload.get("related"):
            out.unrelated += 1
            continue

        events = []
        for item in (payload or {}).get("events") or []:
            idx = item.get("evidence_index")
            if not isinstance(idx, int) or not 1 <= idx <= len(messages):
                # The model cited a message that is not in the chain. Drop the
                # claim rather than guess at one: G2 promises every stage
                # links to real evidence, and an invented index is not that.
                out.bad_evidence_index += 1
                continue
            proof = messages[idx - 1]
            if item["stage"] in _TERMINAL_BY_CONSENT and _CALENDAR_RSVP.match(
                (proof.get("subject") or "").strip()
            ):
                # "Accepted: <name> | <name>" is a calendar RSVP -- two people
                # agreeing to meet, often recruiter to recruiter -- not a
                # candidate accepting an offer. The prompt says so, but a
                # model that reads the literal word still emits it, and one
                # such row put a false offer on a recruiting agency. Only
                # employment paperwork moves an application to accepted, and
                # that is decided by the deterministic rule below.
                out.bad_evidence_index += 1
                continue
            events.append((item["stage"], proof["sent_at"], proof["id"]))

        # Onboarding proves the offer was accepted, and saying so is keyword
        # matching rather than judgment -- so it is decided here instead of
        # being left to the model, which answered "accepted" and then
        # "technical" on successive calls over the identical chain at
        # temperature 0. `enforce_forward_order` below drops whatever
        # interview stages the model put after it.
        onboarding_at = onboarding_accepted_index(
            [m.get("subject") or "" for m in messages]
        )
        if onboarding_at is not None and not any(
            stage == "accepted" for stage, _, _ in events
        ):
            proof = messages[onboarding_at]
            events.append(("accepted", proof["sent_at"], proof["id"]))
            out.onboarding_accepted += 1

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
        if any(e.stage in ("offer", "accepted", "declined") for e in ordered):
            out.offer_signal = True

        if not apply:
            out.events_written += len(ordered)
            continue

        if not ordered:
            # Nothing derived -- a failed call with no onboarding signal, or a
            # chain that genuinely evidences no stage. Leave whatever is on
            # record alone rather than emptying the application.
            continue

        # Append. A derivation never destroys the one before it: rows carry
        # the batch that produced them (`derived_at`) and readers take the
        # newest batch per application, so re-deriving is a pure insert and
        # correcting a bad pass costs nothing but another one. That keeps this
        # table consistent with how the rest of the system stores things --
        # raw mail is write-once and Postgres is a derived view of it (I3) --
        # and it preserves what an earlier pass concluded, which is precisely
        # what you want to look at when a derivation turns out to be wrong.
        #
        # Nothing is written for an application this pass could not derive (a
        # failed call with no onboarding signal, or a chain evidencing no
        # stage): no new batch means the previous one stays newest, so the
        # record keeps whatever it already showed instead of going blank.
        for event in ordered:
            conn.execute(
                "INSERT INTO stage_event (application_id, stage, occurred_at,"
                " evidence_message_id, extracted_by, derived_at)"
                " VALUES (%s, %s, %s, %s, 'timeline', %s)"
                " ON CONFLICT DO NOTHING",
                (
                    app_id,
                    event.stage,
                    event.occurred_at,
                    event.evidence_message_id,
                    batch_at,
                ),
            )
            out.events_written += 1
        conn.commit()

    return out
