"""The dashboard's JSON API (M7, PRD P4/P4.1).

The dashboard is a client app now, so this module is where the record becomes
JSON — and nothing else. Every result set is still one call into
`jobd.services.dashboard` or `jobd.services.timeline` (gate 2), and every
mutation still calls the same service function the CLI calls
(`services.learning.learn_rule` is the clearest case), so there is exactly one
implementation of "list a company's messages" and one of "teach a rule". This
layer's whole job is shaping those results for the wire.

Two properties the server-rendered version got for free and this has to keep
deliberately:

Gate 1 — "paste URL in fresh session, get identical view". Every filter is
still a query parameter; the client reads its state from the URL rather than
from a store, so there is no client-side state a URL could fail to capture.
This module helps by taking the same parameter names the pages use.

No GET is ever a side effect. Reads are GET, writes are POST, and a write
returns the outcome as a message rather than redirecting — the client puts it
in a toast and refetches what it invalidated.

Serialisation is explicit, per type, rather than a generic dataclass dump. The
record's derived fields are the interesting ones — `status`, `classification_
tag`, `is_system`, `ghosted`, an activity cell's `level` — and they are
properties, which any generic encoder silently drops. A page that quietly
loses "is this ghosted" is worse than one that fails to build.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from jobd.config import load_settings
from jobd.ports.chat import (
    ChatEvent,
    Done,
    Error,
    ProposalEvent,
    TextDelta,
    ToolCall,
    ToolResult,
    Turn,
)
from jobd.services import chat as chat_service
from jobd.services import conversations as conversations_service
from jobd.services import dashboard, learning, logos, outbound, summaries, timeline

router = APIRouter(prefix="/api")

PAGE_SIZE = 50


def _connect() -> psycopg.Connection[Any]:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is unset.")
    return psycopg.connect(settings.database_url)


def _repos(conn: psycopg.Connection[Any]) -> Any:
    from jobd.adapters.postgres import repositories

    return repositories(conn)


def _storage() -> Any:
    """Raw-message storage, for resolving a reply's In-Reply-To header off
    the message being replied to (services/outbound.py). Same JOBD_BUCKET
    env var and CachingStorage wrapping the CLI's own `_storage` uses —
    this is a second, independent construction rather than an import from
    `cli.main`, since that module also parses `--bucket`/`--local-store`
    flags that don't exist here."""
    from pathlib import Path

    from jobd.adapters.caching import CachingStorage
    from jobd.adapters.s3 import S3Storage

    bucket = os.environ.get("JOBD_BUCKET")
    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="JOBD_BUCKET is unset — raw message storage isn't configured.",
        )
    cache_dir = Path.home() / ".jobd" / "cache" / "raw"
    return CachingStorage(S3Storage(bucket), cache_dir)


def _sender() -> Any:
    from jobd.adapters.gmail.sender import GmailSender
    from jobd.adapters.secrets import TokenStore

    return GmailSender(TokenStore())


def _self_address(conn: psycopg.Connection[Any]) -> str | None:
    """The account whose mailbox this is.

    Read from the record rather than configured twice: `message.account` is
    what ingestion actually wrote, so it cannot drift from the mail that is
    here. Used to keep the reader out of their own people lists and to refuse
    to teach a rule about their own address.
    """
    row = conn.execute(
        "SELECT account, count(*) FROM message GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


# --------------------------------------------------------------- shaping


def _dt(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _id(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _briefing(row: dashboard.BriefingRow) -> dict[str, Any]:
    return {
        "company_id": _id(row.company_id),
        "canonical_name": row.canonical_name,
        "kind": row.kind,
        "application_id": _id(row.application_id),
        "role_title": row.role_title,
        "latest_stage": row.latest_stage,
        "last_message_at": _dt(row.last_message_at),
        "last_subject": row.last_subject,
        "days_silent": row.days_silent,
        "status": row.status,
    }


def _stats(stats: dashboard.Stats) -> dict[str, Any]:
    return {
        "total_applications": stats.total_applications,
        "total_companies": stats.total_companies,
        "by_outcome": stats.by_outcome,
        "response_rate": stats.response_rate,
        "ghost_rate": stats.ghost_rate,
        "interviews": stats.interviews,
        "interview_hours": stats.interview_hours,
        "prep_hours": stats.prep_hours,
        "offers": stats.offers,
        "rejections": stats.rejections,
        "engaged": stats.engaged,
        "replied": stats.replied,
        "interviewed": stats.interviewed,
        "rounds_30d": stats.rounds_30d,
        "rounds_prev_30d": stats.rounds_prev_30d,
        "outbound_30d": stats.outbound_30d,
        "outbound_prev_30d": stats.outbound_prev_30d,
        "reply_lag_days": stats.reply_lag_days,
        "court_yours": stats.court_yours,
        "court_theirs": stats.court_theirs,
        "weeks_to_offer": stats.weeks_to_offer,
    }


def _home_stats(stats: dashboard.HomeStats) -> dict[str, Any]:
    return {
        "total_messages": stats.total_messages,
        "unclassified": stats.unclassified,
        "negative_filtered": stats.negative_filtered,
        "recorded": stats.recorded,
        "not_job_related": stats.not_job_related,
        "queued_for_review": stats.queued_for_review,
        "companies_by_kind": stats.companies_by_kind,
        "rules_by_source": stats.rules_by_source,
        "review_queue_pending": stats.review_queue_pending,
    }


def _calendar(cal: dashboard.ActivityCalendar) -> dict[str, Any]:
    """The activity grid, already laid out as week-columns of weekday-rows.

    The arithmetic stays on the server for the same reason it was never in a
    template: "pad the leading and trailing partial weeks" is not view code,
    and a client that recomputed it would be a second implementation of the
    graph's shape. `level` travels with the cell because it is the encoding —
    the client renders a ramp step, it does not decide one.
    """
    return {
        "weeks": [
            [
                None
                if cell is None
                else {
                    "day": cell.day.isoformat(),
                    "count": cell.count,
                    "level": cell.level,
                }
                for cell in week
            ]
            for week in cal.weeks
        ],
        "month_labels": [[i, label] for i, label in cal.month_labels],
        "total": cal.total,
        "active_days": cal.active_days,
        "busiest": None
        if cal.busiest is None
        else {"day": cal.busiest.day.isoformat(), "count": cal.busiest.count},
        "since": cal.since.isoformat(),
        "until": cal.until.isoformat(),
        "metric": cal.metric,
    }


#: The global time range, parsed off the query string on every windowed
#: route. Grafana-style: the client sends absolute ISO instants, so a range
#: means the same thing on every request regardless of when it is served --
#: "last 6 months" resolved once in the browser rather than re-resolved per
#: endpoint, which would let two panels on one screen disagree about where
#: the window starts.
#:
#: Absent or unparseable means unbounded, which is the all-time view.
def _range(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    def parse(value: str | None) -> datetime | None:
        if not value:
            return None
        # A `+` in a query string decodes to a space, so "…T00:00+00:00"
        # arrives as "…T00:00 00:00" whenever a caller (curl, a hand-written
        # link) skipped percent-encoding. Browsers encode it correctly, but
        # the failure mode if they didn't is silent: an unparseable bound
        # falls back to all-time, so the page would quietly report on the
        # whole record while the header claimed a window.
        text = value.strip().replace("Z", "+00:00")
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            # Retry with a trailing " 00:00" read back as the "+00:00" it
            # was before the query string decoded the plus. Only the offset
            # is patched -- a space between date and time is valid ISO that
            # `fromisoformat` already accepted above, so it never gets here.
            try:
                stamp = datetime.fromisoformat(
                    re.sub(r" (\d{2}:\d{2})$", r"+\1", text)
                )
            except ValueError:
                return None
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)

    return parse(since), parse(until)


@router.get("/activity")
def activity(
    metric: str = "interviews",
    company: UUID | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """The activity grid on its own, so the graph's metric picker can swap
    series without refetching the page that hosts it. `interviews` counts
    confirmed rounds; `messages` counts inbound job-linked mail per day."""
    if metric not in ("interviews", "messages"):
        raise HTTPException(status_code=422, detail="metric must be interviews or messages.")
    start, end = _range(since, until)
    with _connect() as conn:
        return _calendar(
            dashboard.activity_calendar(
                conn,
                company_id=company,
                metric=metric,
                since=start.date() if start else None,
                until=end.date() if end else None,
                span_record=start is None,
            )
        )


def _company_row(row: dashboard.CompanyRow) -> dict[str, Any]:
    return {
        "id": _id(row.id),
        "canonical_name": row.canonical_name,
        "domain": row.domain,
        "kind": row.kind,
        "message_count": row.message_count,
        "application_count": row.application_count,
        "last_touch": _dt(row.last_touch),
        "last_direction": row.last_direction,
        "latest_stage": row.latest_stage,
        "turn": dashboard.whose_turn(row.last_direction),
    }


def _communication(row: dashboard.CommunicationRow) -> dict[str, Any]:
    return {
        "id": _id(row.id),
        "channel": row.channel,
        "direction": row.direction,
        "sent_at": _dt(row.sent_at),
        "subject": row.subject,
        "contact_name": row.contact_name,
        "contact_address": row.contact_address,
        "application_id": _id(row.application_id),
        "role_title": row.role_title,
        "is_stage_evidence": row.is_stage_evidence,
        "company_name": row.company_name,
        "stages": list(row.stages),
        "link_role": row.link_role,
        "classification_tag": row.classification_tag,
        "body_text": row.body_text,
    }


def _claim(claim: timeline.Claim) -> dict[str, Any]:
    return {
        "stage": claim.stage,
        "occurred_at": _dt(claim.occurred_at),
        "evidence_message_id": _id(claim.evidence_message_id),
        "evidence_subject": claim.evidence_subject,
        "evidence_direction": claim.evidence_direction,
        "confidence": claim.confidence,
        "extracted_by": claim.extracted_by,
    }


def _application(app: timeline.ApplicationTimeline, *, now: datetime) -> dict[str, Any]:
    return {
        "application_id": _id(app.application_id),
        "role_title": app.role_title,
        "started_at": _dt(app.started_at),
        "ended_at": _dt(app.ended_at),
        "outcome": app.outcome,
        "message_count": app.message_count,
        "last_message_at": _dt(app.last_message_at),
        "last_direction": app.last_direction,
        # Derived at read time, exactly as `record.is_ghosted` intends, and
        # stored nowhere: nobody sends the message that means "we have stopped
        # replying". Sent as a field so the client never re-derives a rule the
        # record owns.
        "ghosted": app.ghosted(now=now),
        "claims": [_claim(c) for c in app.claims],
    }


def _timeline(tl: timeline.CompanyTimeline, *, now: datetime) -> dict[str, Any]:
    return {
        "company_id": _id(tl.company_id),
        "canonical_name": tl.canonical_name,
        "domain": tl.domain,
        "kind": tl.kind,
        "first_seen_at": _dt(tl.first_seen_at),
        "last_seen_at": _dt(tl.last_seen_at),
        "unlinked_messages": tl.unlinked_messages,
        "applications": [_application(a, now=now) for a in tl.applications],
    }


def _person(person: dashboard.PersonRow) -> dict[str, Any]:
    return {
        "contact_id": _id(person.contact_id),
        "name": person.name,
        "address": person.address,
        "channel": person.channel,
        "role_title": person.role_title,
        "message_count": person.message_count,
        "first_at": _dt(person.first_at),
        "last_at": _dt(person.last_at),
        "stages": list(person.stages),
        "company_count": person.company_count,
        "automated_reason": person.automated_reason,
        "is_system": person.is_system,
    }


def _verification(row: dashboard.VerificationRow) -> dict[str, Any]:
    return {
        "id": _id(row.id),
        "model": row.model,
        "message_count": row.message_count,
        "classification_correct": row.classification_correct,
        "verified_name": row.verified_name,
        "verified_kind": row.verified_kind,
        "verified_domain": row.verified_domain,
        "reasoning": row.reasoning,
        "suggested_rules": row.suggested_rules,
        "created_at": _dt(row.created_at),
    }


def _detail(
    detail: dashboard.MessageDetail, *, self_address: str | None
) -> dict[str, Any]:
    return {
        "id": _id(detail.id),
        "subject": detail.subject,
        "sent_at": _dt(detail.sent_at),
        "direction": detail.direction,
        "channel": detail.channel,
        "sender_address": detail.sender_address,
        "recipient_addresses": list(detail.recipient_addresses),
        "thread_id": detail.thread_id,
        "body_text": detail.body_text,
        "tag": detail.tag,
        "classified_by": detail.classified_by,
        "company_id": _id(detail.company_id),
        "company_name": detail.company_name,
        "contact_id": _id(detail.contact_id),
        "contact_name": detail.contact_name,
        "contact_address": detail.contact_address,
        "evidences": [[stage, role] for stage, role in detail.evidences],
        # Teaching a rule about your own address would match the whole
        # mailbox, so the client is told which address is the reader's rather
        # than being trusted to work it out.
        "correspondent": detail.correspondent,
        "self_address": self_address,
    }


def _rows(rows: list[Any]) -> list[list[Any]]:
    """A list of raw tuples (the ingest log) as JSON-safe lists."""
    return [
        [v.isoformat() if isinstance(v, (datetime, date)) else v for v in row]
        for row in rows
    ]


def _jsonable(value: Any) -> Any:
    """Dicts straight from SQL still carry UUIDs and datetimes."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ----------------------------------------------------------------- reads


#: Models the chat panel's picker offers, beyond whatever JOBD_CHAT_MODEL is
#: set to (always included, always first — it's the operator's own default).
#: A curated allowlist, not "any LiteLLM id a request names": the panel POSTs
#: a plain string with no further check on the server past membership here,
#: so this list is the entire access-control surface for "what can the chat
#: send my mail to". Three real, tested models from this session's own
#: classify work — cheap-fast, cheap-strong, and a bigger second opinion.
_CHAT_MODEL_CHOICES = (
    "openrouter/google/gemini-3.7-flash",
    "openrouter/google/gemini-2.5-flash-lite",
    "openrouter/z-ai/glm-5.2",
)


def _chat_models() -> list[str]:
    default = load_settings().chat_model
    choices = list(_CHAT_MODEL_CHOICES)
    if default and default not in choices:
        choices.insert(0, default)
    return choices


@router.get("/chrome")
def chrome() -> dict[str, Any]:
    """What the app shell needs and no page owns: the nav badge, whether chat
    exists at all, and whether raw-message storage (JOBD_BUCKET, `_storage()`
    above) is configured — the reply box needs to know this before a human
    clicks Save draft and hits a 503, not just after. Separate from every
    page payload so switching pages does not refetch it."""
    with _connect() as conn:
        return {
            "pending": dashboard.triage_counts(conn)["pending"],
            "chat_model": load_settings().chat_model,
            "chat_models": _chat_models() if load_settings().chat_model else [],
            "storage_configured": bool(os.environ.get("JOBD_BUCKET")),
        }


@router.get("/runs")
def runs(limit: int = 25) -> dict[str, Any]:
    """Live pipeline runs (`pipeline_run` rows) with derived rate/ETA/stall —
    the Runs page polls this every 2s while anything is active."""
    from jobd.services.metrics import read_runs

    with _connect() as conn:
        return {"runs": read_runs(conn, limit=min(limit, 100))}


@router.get("/thread-extractions")
def thread_extractions(limit: int = 200, label: str | None = None) -> dict[str, Any]:
    """The one-call-per-thread model readings (`thread_extraction`), newest
    first — the Classifications page's table. One row per thread the model
    actually read; everything resolved free never appears here, which is
    the point of the page."""
    where = ""
    params: list[Any] = []
    if label:
        where = "WHERE te.label = %s"
        params.append(label)
    params.append(min(limit, 1000))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT te.thread_id, te.label, te.company_name, te.company_domain,
                   te.company_kind, te.agency_name, te.agency_domain,
                   te.role_title, te.stage, te.relationship,
                   te.client_companies, te.messages_covered, te.model,
                   te.updated_at, m.subject, m.sender_address
            FROM thread_extraction te
            LEFT JOIN message m ON m.id = te.latest_message_id
            {where}
            ORDER BY te.updated_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
        counts = dict(
            conn.execute(
                "SELECT label, count(*) FROM thread_extraction GROUP BY label"
            ).fetchall()
        )
    return {
        "counts": counts,
        "threads": [
            {
                "thread_id": r[0],
                "label": r[1],
                "company_name": r[2],
                "company_domain": r[3],
                "company_kind": r[4],
                "agency_name": r[5],
                "agency_domain": r[6],
                "role_title": r[7],
                "stage": r[8],
                "relationship": r[9],
                "client_companies": r[10] or [],
                "messages_covered": r[11],
                "model": r[12],
                "updated_at": r[13].isoformat() if r[13] else None,
                "subject": r[14],
                "sender": r[15],
            }
            for r in rows
        ],
    }


@router.get("/logos")
def logo_index() -> dict[str, Any]:
    """Which companies have a cached logo, in one list.

    Asked once by the client and kept, rather than requesting a logo per
    company and taking a 404 for most: 577 companies is 577 round trips to
    learn that a few hundred of them have nothing. `coverage` is here for the
    same reason the semantic search route carries its own — a reader looking
    at a page of monograms should be able to tell "no logo exists" from
    "`jobd logos` has never been run".
    """
    with _connect() as conn:
        return {
            "ids": logos.have_logos(conn),
            "coverage": logos.coverage(conn),
        }


@router.get("/company/{company_id}/logo")
def company_logo(company_id: UUID) -> Response:
    """One company's cached logo, from this machine.

    The bytes were captured by `jobd logos`; nothing here reaches the
    network. Cached hard by the browser because a logo for a given company
    does not change between page views, and a miss is cached briefly so a
    stale client cannot turn one missing logo into a request per render.
    """
    with _connect() as conn:
        found = logos.logo(conn, company_id)
    if found is None:
        return Response(status_code=404, headers={"Cache-Control": "max-age=300"})
    image, content_type = found
    return Response(
        content=image,
        media_type=content_type,
        headers={"Cache-Control": "max-age=86400"},
    )


@router.get("/home")
def home(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """What needs you, and nothing else.

    Three lists a job search actually runs on, the activity graph above
    them, and one line of queue depth below, so the briefing never pretends
    the record is clean. Offers get their own page (`/offers` below), not a
    fourth list here — `in_flight` already includes a live offer among five
    other mid-process stages, and a decision worth making deserves its own
    address, not a section on a page that's mostly about something else.
    """
    start, end = _range(since, until)
    with _connect() as conn:
        return {
            "calendar": _calendar(
                dashboard.activity_calendar(
                    conn,
                    since=start.date() if start else None,
                    until=end.date() if end else None,
                    span_record=start is None,
                )
            ),
            "waiting": [
                _briefing(r)
                for r in dashboard.awaiting_your_reply(conn, since=start, until=end)
            ],
            "cold": [_briefing(r) for r in dashboard.going_cold(conn)],
            # A count, not a capped list — Home no longer renders in-flight
            # rows at all (only a "Moving" stat), so there's nothing left to
            # cap; see in_flight_count's docstring for the bug this replaced.
            "moving_count": dashboard.in_flight_count(conn),
            "stats": _stats(dashboard.compute_stats(conn, since=start, until=end)),
            "counts": dashboard.triage_counts(conn),
        }


@router.get("/offers")
def offers(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Every application that ever received an offer — its own address, not
    folded into `/home` (see that route's docstring)."""
    with _connect() as conn:
        start, end = _range(since, until)
        return {
            "offers": [
                _briefing(r)
                for r in dashboard.offers(conn, limit=200, since=start, until=end)
            ]
        }






@router.get("/waiting")
def waiting(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Every company whose last message came *in* — your reply is owed.

    Home carries the same list bounded to a briefing (25 rows, 120 days).
    This is the whole backlog, which is a different job: Home answers "what
    should I do today", this answers "who am I still on the hook for", and
    582 rows would drown the first question while a 25-row cap would answer
    the second wrongly.
    """
    start, end = _range(since, until)
    with _connect() as conn:
        return {
            "waiting": [
                _briefing(r)
                for r in dashboard.awaiting_your_reply(
                    conn, within_days=None, limit=500, since=start, until=end
                )
            ]
        }


@router.get("/rejections")
def rejections(
    since: str | None = None, until: str | None = None
) -> dict[str, Any]:
    """Every application the company said no to — its own address, same
    reasoning as `/offers`."""
    start, end = _range(since, until)
    with _connect() as conn:
        return {
            "rejections": [
                _briefing(r)
                for r in dashboard.rejections(conn, limit=200, since=start, until=end)
            ]
        }


@router.get("/companies")
def companies(
    sort: str = "recency",
    search: str | None = None,
    kind: str | None = None,
    turn: str | None = None,
    active: int | None = None,
    substantive: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    start, end = _range(since, until)
    with _connect() as conn:
        rows = dashboard.list_companies(
            conn,
            sort=sort,
            substantive=substantive == "1",
            search=search or None,
            kind=kind or None,
            turn=turn or None,
            active_within_days=active or None,
            since=start,
            until=end,
        )
        return {
            "companies": [_company_row(r) for r in rows],
            # Same filters as the list above, not a bare unfiltered count —
            # real bug, found reading a live browse of the app: this used to
            # call count_companies() with no arguments at all, so "0 of 509"
            # showed for a search matching nothing and "38 of 509" for
            # kind=agency instead of "38 of 38". The count is only ever
            # correct when it's asked the same question as the list.
            "total": dashboard.count_companies(
                conn,
                search=search or None,
                kind=kind or None,
                turn=turn or None,
                substantive=substantive == "1",
                active_within_days=active or None,
                since=start,
                until=end,
            ),
            "stats": _stats(dashboard.compute_stats(conn, since=start, until=end)),
        }


@router.get("/company/{company_id}")
def company(
    company_id: UUID,
    channel: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    order: str = "desc",
    stage_evidencing: str | None = Query(default=None),
) -> dict[str, Any]:
    with _connect() as conn:
        try:
            tl = timeline.build(conn, company_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such company.") from None
        evidencing = (
            None if stage_evidencing in (None, "") else stage_evidencing == "true"
        )
        comms = dashboard.list_communications(
            conn,
            company_id=company_id,
            channel=channel or None,
            direction=direction or None,
            search=search or None,
            stage_evidencing=evidencing,
            order=order,
        )
        verification = dashboard.latest_verification(conn, company_id)
        return {
            "timeline": _timeline(tl, now=datetime.now(UTC)),
            "communications": [_communication(c) for c in comms],
            # One scalar query, replacing a full aggregate over every company
            # that was scanned to read a single field.
            "turn": dashboard.company_turn(conn, company_id),
            "people": [
                _person(p)
                for p in dashboard.people_for_company(
                    conn, company_id, self_address=_self_address(conn)
                )
            ],
            "calendar": _calendar(
                dashboard.activity_calendar(conn, company_id=company_id)
            ),
            "verification": _verification(verification) if verification else None,
        }


def _summary_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "markdown": row["markdown"],
        "model": row["model"],
        "created_at": _dt(row["created_at"]),
        "stale": row["fingerprint"] != row["current_fingerprint"],
    }


@router.get("/company/{company_id}/summary")
def company_summary(company_id: UUID) -> dict[str, Any]:
    """The cached summary, if one exists — never triggers a model call.
    `null` means nothing has been generated yet, not that generation
    failed; the panel starts a stream either way (company.tsx auto-triggers
    on mount when this comes back stale or empty)."""
    with _connect() as conn:
        row = summaries.cached_summary(conn, company_id)
        return {"summary": _summary_json(row) if row else None}


@router.post("/company/{company_id}/summary/stream")
def stream_company_summary(company_id: UUID, request: Request) -> StreamingResponse:
    """Stream a fresh summary as SSE — the only path in this file that calls
    a model outside the chat's read-only connection, and it runs on a normal
    read-write connection used only to write the cache row (docs/chat-and-
    summaries.md §2). `model`/`web` arrive as query params rather than a
    JSON body: `EventSource` (and this app's own SSE fetch helper) can't
    send one on the initiating request."""
    settings = load_settings()
    if not settings.chat_model:
        raise HTTPException(status_code=404, detail="No model is configured.")
    requested = request.query_params.get("model")
    model = requested if requested in _chat_models() else settings.chat_model
    if request.query_params.get("web") == "1" and not model.endswith(":online"):
        model = f"{model}:online"

    from jobd.adapters.llm import load_provider

    provider = load_provider(model, max_tokens=8192)

    def stream() -> Iterator[str]:
        conn = _connect()
        try:
            for event in summaries.stream_company_summary(conn, company_id, cast(Any, provider)):
                yield _sse(event)
        except Exception as exc:  # a stuck/failed call must not hang the panel
            from jobd.ports.chat import Error

            yield _sse(Error(str(exc)))
        finally:
            conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/messages")
def messages(
    search: str | None = None,
    tag: str | None = None,
    recorded: str | None = None,
    direction: str | None = None,
    channel: str | None = None,
    company: UUID | None = None,
    on: str | None = None,
    show_negative: str | None = None,
    order: str = "desc",
    page: int = 1,
    range_since: str | None = Query(default=None, alias="since"),
    range_until: str | None = Query(default=None, alias="until"),
) -> dict[str, Any]:
    """Every message, not just the 2% that resolved to a company.

    Mail the deterministic layer resolved to not-job-related is hidden by
    default — that is, definitionally, mail nobody wants staring back at them.
    `show_negative=1` is still the only route into the messages the prefilter
    rejected, which is the only place a false negative can be found: an
    explicit opt-in, never the default.
    """
    # The global range, unless `on` names a single day -- a day is a
    # narrower ask than the window and answering with the window instead
    # would ignore the more specific question.
    since, until = _range(range_since, range_until)
    if on:
        try:
            day = date.fromisoformat(on)
            since = datetime.combine(day, time.min, tzinfo=UTC)
            until = datetime.combine(day, time.max, tzinfo=UTC)
        except ValueError:
            on = None

    has_company = None if recorded in (None, "") else recorded == "1"
    with _connect() as conn:
        filters: dict[str, Any] = {
            "company_id": company,
            "search": search or None,
            "tag": tag or None,
            "has_company": has_company,
            "hide_negative": not show_negative,
            "direction": direction or None,
            "channel": channel or None,
            "since": since,
            "until": until,
        }
        total = dashboard.count_communications(conn, **filters)
        pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        # Clamped, not just floored: `?page=99999` used to render an empty
        # list with no pager at all, stranding the reader on a dead page.
        page = min(max(page, 1), pages)
        rows = dashboard.list_communications(
            conn, order=order, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, **filters
        )
        return {
            "messages": [_communication(r) for r in rows],
            "total": total,
            "page": page,
            "pages": pages,
        }


@router.get("/search")
def search(
    q: str = "",
    company: UUID | None = None,
    like: UUID | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Search the record by meaning rather than by word.

    Two probes, one shape of answer:

    `q` is text. It gets embedded — one call, the only cost on this route —
    and compared against `message.embedding`. `like` is a message id and
    costs nothing at all: that message's stored vector is the probe, which
    is what makes "more like this" free to click as many times as you want.
    `like` wins if both are given, because clicking a result is a newer
    intent than the box you typed in a minute ago.

    Every response carries `coverage`, unasked. Semantic search can only see
    embedded messages — 2,632 of 58,331 today, i.e. the recorded ones — and
    a reader who does not know that will read an empty result as "the record
    contains nothing about this" when it means "nothing about this was ever
    embedded". The number is the difference between those two sentences.

    `shared_words` per hit is the honest accounting of what the embedding
    bought: the query's own content words that actually appear in that
    message. An empty list means full-text search could not have found this
    row at all.
    """
    with _connect() as conn:
        coverage = dashboard.embedding_coverage(conn)
        capped = max(1, min(limit, 50))
        seed: dict[str, Any] | None = None
        hits: list[dashboard.SemanticHit] = []

        if like is not None:
            detail = dashboard.message_detail(conn, like)
            seed = (
                {"id": _id(like), "subject": detail.subject if detail else None}
                if detail
                else {"id": _id(like), "subject": None}
            )
            hits = dashboard.semantic_neighbours(conn, like, limit=capped)
            embedded_now = False
        elif q.strip():
            settings = load_settings()
            if not settings.chat_model:
                # Same 404 the chat route gives: the feature does not exist
                # on this deployment rather than being broken on it.
                raise HTTPException(
                    status_code=404,
                    detail="No model is configured, so a query cannot be embedded.",
                )
            from jobd.adapters.llm import load_provider

            provider = cast(Any, load_provider(settings.chat_model))
            try:
                vector = provider.embed([q])[0]
            except Exception as exc:  # a provider outage is not a 500 page
                raise HTTPException(
                    status_code=502, detail=f"Could not embed that query: {exc}"
                ) from exc
            hits = dashboard.semantic_search_ranked(
                conn, vector, query_text=q, company_id=company, limit=capped
            )
            embedded_now = True
        else:
            embedded_now = False

        # Which companies this concept belongs to, counted off the same
        # result set rather than re-queried — the answer to "who keeps
        # talking about this", which the ranked list buries.
        tally: dict[str, dict[str, Any]] = {}
        for hit in hits:
            if hit.company_id is None:
                continue
            key = str(hit.company_id)
            entry = tally.setdefault(
                key, {"id": key, "name": hit.company_name, "count": 0}
            )
            entry["count"] += 1

        return {
            "query": q,
            "like": seed,
            "embedded_query": embedded_now,
            "coverage": coverage,
            "companies": sorted(
                tally.values(), key=lambda e: (-e["count"], e["name"] or "")
            ),
            "hits": [
                {
                    **_communication(hit.row),
                    "company_id": _id(hit.company_id),
                    "company_name": hit.company_name,
                    "distance": round(hit.distance, 4),
                    "similarity": round(1.0 - hit.distance, 4),
                    "shared_words": list(hit.shared_words),
                }
                for hit in hits
            ],
        }


@router.get("/message/{message_id}")
def message(message_id: UUID) -> dict[str, Any]:
    """One message, opened.

    Fetched when a row is first expanded rather than joined into the list:
    bodies average 3.5k characters and a page lists fifty rows, so inlining
    them would send half a megabyte to render a list of subjects.
    """
    with _connect() as conn:
        detail = dashboard.message_detail(conn, message_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="No such message.")
        return _detail(detail, self_address=_self_address(conn))


def _outbound_json(row: outbound.OutboundRow) -> dict[str, Any]:
    return {
        "id": _id(row.id),
        "status": row.status,
        "subject": row.subject,
        "body": row.body,
        "recipients": list(row.recipients),
        "sent_message_id": row.sent_message_id,
        "approved_by": row.approved_by,
        "approved_at": _dt(row.approved_at),
        "created_at": _dt(row.created_at),
    }


class ReplyDraftBody(BaseModel):
    #: Comma-separated, not a list — `Proposal.fields` (ports/chat.py) is
    #: `dict[str, str]`, the same "pre-filled `<form>`" shape every other
    #: proposal already posts, so the chat's `propose_draft_reply` tool can
    #: hit this exact endpoint without a special-cased field type.
    to: str
    subject: str
    body: str
    #: Same comma-separated convention as `to`. Optional because a plain
    #: Reply has no Cc line at all.
    cc: str = ""


class SuggestReplyBody(BaseModel):
    #: Free text, folded into the prompt as its own tagged block (domain/
    #: reply_suggest.py) rather than concatenated into the base prompt —
    #: keeps it a labelled instruction the model follows, not a string an
    #: operator has to know is being spliced into a bigger one.
    prompt: str | None = None


@router.post("/message/{message_id}/reply/suggest")
def suggest_reply(message_id: UUID, body: SuggestReplyBody) -> dict[str, Any]:
    """Draft (or redraft) a reply body with the model — no side effects, no
    draft written anywhere; the result is just handed back for the reply
    box's own textarea, same as the AI summary's suggested reply already is
    when a human copies it over by hand. `body.prompt` is optional
    instructions from the human, folded in for this call only."""
    settings = load_settings()
    if not settings.chat_model:
        raise HTTPException(status_code=404, detail="No model is configured.")

    from jobd.adapters.llm import load_provider
    from jobd.services import reply_suggest as reply_suggest_service

    provider = load_provider(settings.chat_model)
    with _connect() as conn:
        if _repos(conn).messages.get(message_id) is None:
            raise HTTPException(status_code=404, detail="No such message.")
        try:
            text = reply_suggest_service.suggest_reply(
                conn, cast(Any, provider), message_id, extra_instructions=body.prompt
            )
        except Exception as exc:  # a provider outage is not a 500 page
            return _err(str(exc))
    if text is None:
        return _err("Model returned nothing usable.")
    return {"ok": True, "body": text}


@router.get("/message/{message_id}/reply/context")
def reply_context(message_id: UUID) -> dict[str, Any]:
    """Who a Reply and a Reply-all to this message would address, resolved
    server-side from the stored raw bytes (domain/reply.py) — the same
    Reply-To-over-From and everyone-minus-you rules Gmail itself applies —
    so the reply box pre-fills what Gmail's own Reply buttons would, while
    every line stays editable client-side before anything is drafted."""
    from email import message_from_bytes, policy

    from jobd.domain import reply as reply_rules

    with _connect() as conn:
        message = _repos(conn).messages.get(message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="No such message.")
        try:
            raw = _storage().get(message.storage_key)
        except HTTPException:
            raise
        except Exception as exc:  # a storage outage is not a 500 page
            return _err(str(exc))
        parsed = message_from_bytes(raw.payload, policy=policy.default)
        selves = {message.account}
        one = reply_rules.reply_recipients(
            parsed, self_addresses=selves, reply_all=False
        )
        all_ = reply_rules.reply_recipients(
            parsed, self_addresses=selves, reply_all=True
        )
        subject = str(parsed.get("Subject", "")) or message.subject
        return {
            "ok": True,
            "subject": reply_rules.reply_subject(subject),
            "reply": {"to": list(one.to), "cc": list(one.cc)},
            "reply_all": {"to": list(all_.to), "cc": list(all_.cc)},
        }


@router.post("/message/{message_id}/reply/draft")
def draft_reply(message_id: UUID, body: ReplyDraftBody) -> dict[str, Any]:
    """Write a reply to this message's own channel drafts folder, and record
    it — never sent from here. `to`/`subject`/`body` are already resolved by
    the caller (typically pre-filled from the message's correspondent and
    the AI summary's suggested reply, both editable before this is called)
    rather than re-derived server-side, so what gets drafted is exactly what
    the human read on screen before clicking."""
    with _connect() as conn:
        message = _repos(conn).messages.get(message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="No such message.")
        try:
            row = outbound.create_draft(
                conn,
                _sender(),
                _storage(),
                account=message.account,
                to=[t.strip() for t in body.to.split(",") if t.strip()],
                cc=[t.strip() for t in body.cc.split(",") if t.strip()],
                subject=body.subject.strip(),
                body=body.body,
                in_reply_to_message_id=message_id,
            )
        except outbound.OutboundError as exc:
            return _err(str(exc))
        except Exception as exc:  # SendNotAuthorized and real transport errors alike
            return _err(str(exc))
        return {"ok": True, "draft": _outbound_json(row)}


@router.post("/outbound/{outbound_id}/send")
def send_reply(outbound_id: UUID) -> dict[str, Any]:
    """The only path that ever calls `Sender.send` — this request itself
    *is* the recorded human approval (I1); nothing upstream of a human's
    own click on this endpoint can reach it, chat included (SECURITY.md
    §4, docs/chat-and-summaries.md §2)."""
    with _connect() as conn:
        try:
            row = outbound.approve_and_send(
                conn,
                _sender(),
                outbound_id=outbound_id,
                approved_by=_self_address(conn) or "unknown",
            )
        except outbound.OutboundError as exc:
            return _err(str(exc))
        except Exception as exc:
            return _err(str(exc))
        return {"ok": True, "draft": _outbound_json(row)}


@router.get("/contact/{contact_id}")
def contact(contact_id: UUID) -> dict[str, Any]:
    """One person, across every company they touched.

    The payoff for `contact_company` being a time-bounded table rather than a
    column on `contact`: a recruiter who mailed you from three firms keeps all
    three relationships instead of the newest overwriting the rest.
    """
    with _connect() as conn:
        found = dashboard.contact_detail(conn, contact_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such contact.")
        display_name, identities, companies_ = found
        return {
            "display_name": display_name,
            "identities": [[channel, address] for channel, address in identities],
            "companies": [
                {
                    "company_id": _id(c.company_id),
                    "canonical_name": c.canonical_name,
                    "kind": c.kind,
                    "role_title": c.role_title,
                    "message_count": c.message_count,
                    "first_seen_at": _dt(c.first_seen_at),
                    "last_seen_at": _dt(c.last_seen_at),
                }
                for c in companies_
            ],
            "messages": [
                _communication(m)
                for m in dashboard.list_communications(
                    conn, contact_id=contact_id, limit=50
                )
            ],
        }


@router.get("/triage")
def triage(
    tab: str = "queue",
    reason: str | None = None,
    item: UUID | None = None,
    source: str | None = None,
    verdict: str | None = None,
    flagged: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """The decision workbench: the queue, the senders, the rules, the audits.

    One endpoint per tab would be four round trips for a strip of counts every
    tab shows. An unknown tab falls back to the queue rather than returning an
    empty payload, so a stale link stays useful.
    """
    page = max(page, 1)
    if tab not in ("queue", "senders", "rules", "audits"):
        tab = "queue"
    with _connect() as conn:
        payload: dict[str, Any] = {
            "tab": tab,
            "counts": dashboard.triage_counts(conn),
            "page": page,
        }
        if tab == "queue":
            items = dashboard.pending_reviews(
                conn, reason=reason, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
            )
            selected = None
            if items:
                target = item or items[0]["message_id"]
                if item is not None:
                    match = [i for i in items if i["id"] == item]
                    target = match[0]["message_id"] if match else items[0]["message_id"]
                found = dashboard.message_detail(conn, target)
                selected = (
                    _detail(found, self_address=_self_address(conn)) if found else None
                )
            payload |= {
                "items": _jsonable(items),
                "reasons": [[text, n] for text, n in dashboard.review_reasons(conn)],
                "selected": selected,
                # What the Next/Previous control (triage.tsx) paginates
                # against — real bug, live-caught: nothing before this told
                # the client how many pages existed, so page 2+ was
                # unreachable the moment the queue passed PAGE_SIZE.
                "total": dashboard.pending_reviews_count(conn, reason=reason),
                "page_size": PAGE_SIZE,
            }
        elif tab == "senders":
            payload |= {
                "coverage": dashboard.sender_graph_coverage(conn),
                "unresolved": _jsonable(
                    _repos(conn).messages.top_unresolved_correspondents(40)
                ),
                "categories": _jsonable(dashboard.list_categories(conn)),
            }
        elif tab == "rules":
            payload |= {
                "rules": _jsonable(
                    dashboard.list_rules(conn, source=source, verdict=verdict)
                ),
                "categories": _jsonable(dashboard.list_categories(conn)),
            }
        elif tab == "audits":
            payload |= {
                "verifications": _jsonable(
                    dashboard.list_verifications(conn, only_flagged=flagged == "1")
                )
            }
        return payload


@router.get("/pipeline")
def pipeline() -> dict[str, Any]:
    """The pipeline's own read of the mailbox — coverage, spend, ingest health.

    `ingest_run` has 589 rows and had no surface at all, which is the thing
    migration 0004 was written to prevent.
    """
    with _connect() as conn:
        health = dashboard.ingest_health(conn)
        return {
            "home_stats": _home_stats(dashboard.compute_home_stats(conn)),
            "stats": _stats(dashboard.compute_stats(conn)),
            "coverage": dashboard.sender_graph_coverage(conn),
            "ingest": {
                "runs": health.runs,
                "failures": health.failures,
                "last_run_at": _dt(health.last_run_at),
                "days_covered": health.days_covered,
                "first_day": _dt(health.first_day),
                "last_day": _dt(health.last_day),
                "missing_days": health.missing_days,
                "recent": _rows(health.recent),
            },
        }


# ---------------------------------------------------------------- writes
#
# Each one returns the outcome instead of redirecting. The redirect existed to
# carry a flash through a page load; a client that already knows which queries
# a write invalidates does not need one, and `ok` is what decides whether the
# toast is an error.


def _ok(message: str) -> dict[str, Any]:
    return {"ok": True, "message": message}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


class TeachBody(BaseModel):
    domain: str | None = None
    address: str | None = None
    verdict: str = "negative"
    company: str | None = None
    company_domain: str | None = None
    category: str | None = None
    kind: str = "employer"


@router.post("/teach")
def teach(body: TeachBody) -> dict[str, Any]:
    """Teach one sender rule, and fan it out over the backlog.

    Calls `services.learning.learn_rule` — the same function `jobd learn`
    calls, not a second implementation of it.
    """
    with _connect() as conn:
        if body.address and body.address.lower() == (_self_address(conn) or "").lower():
            return _err("That is your own address.")
        try:
            result = learning.learn_rule(
                conn,
                _repos(conn),
                domain=body.domain or None,
                address=body.address or None,
                verdict=body.verdict,  # type: ignore[arg-type]
                company=body.company or None,
                company_domain=body.company_domain or None,
                category=body.category or None,
                kind=body.kind,
            )
        except learning.LearnError as error:
            return _err(str(error))
    return _ok(
        f"Learned {result.match_type}={result.value} → {result.verdict}."
        f" Resolved {result.resolved} backlog message(s)."
    )


class ConversationBody(BaseModel):
    """An offline conversation, as typed by the person who had it."""

    company_id: UUID
    occurred_at: str
    kind: str = "phone"
    counterpart: str | None = None
    notes: str = ""
    stage: str | None = None


@router.get("/conversations")
def conversations(
    company_id: UUID | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Recorded offline conversations, most recent first."""
    range_since, range_until = _range(since, until)
    with _connect() as conn:
        rows = conversations_service.listing(
            conn, company_id=company_id, since=range_since, until=range_until
        )
    return {
        "conversations": [
            {
                "id": str(r.id),
                "company_id": str(r.company_id),
                "company_name": r.company_name,
                "application_id": str(r.application_id) if r.application_id else None,
                "occurred_at": r.occurred_at.isoformat(),
                "kind": r.kind,
                "counterpart": r.counterpart,
                "notes": r.notes,
                "stage": r.stage,
            }
            for r in rows
        ],
        "kinds": list(conversations_service.KINDS),
        "stages": list(conversations_service.STAGES),
    }


@router.post("/conversations")
def add_conversation(body: ConversationBody) -> dict[str, Any]:
    """Record one conversation, and the stage it establishes if it has one.

    The stage is written as a real `stage_event` (`extracted_by='manual'`), so
    a verbal offer reaches the funnel and the timeline by the same road a
    derived one does — see `services.conversations` for why it must carry the
    application's batch stamp to be visible at all.
    """
    occurred, _ = _range(body.occurred_at, None)
    if occurred is None:
        return _err("Could not read that date.")
    try:
        with _connect() as conn:
            new_id = conversations_service.record(
                conn,
                company_id=body.company_id,
                occurred_at=occurred,
                kind=body.kind,
                notes=body.notes,
                counterpart=body.counterpart or None,
                stage=body.stage or None,
            )
    except ValueError as error:
        return _err(str(error))
    except psycopg.errors.UniqueViolation:
        # `stage_event_manual_key` (migration 0033). Re-submitting the same
        # call is the common way to hit this — a double-click, or a second go
        # after a slow save — so say what happened rather than 500.
        return _err("That stage is already recorded for this company on that date.")
    note = f" Recorded {body.stage}." if body.stage else ""
    return {"ok": True, "message": f"Conversation saved.{note}", "id": str(new_id)}


@router.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: UUID) -> dict[str, Any]:
    """Delete a conversation, and the stage it asserted."""
    with _connect() as conn:
        if not conversations_service.delete(conn, conversation_id):
            return _err("No such conversation.")
    return _ok("Conversation removed.")


class ResolveBody(BaseModel):
    review_id: UUID
    decision: str


@router.post("/review/resolve")
def review_resolve(body: ResolveBody) -> dict[str, Any]:
    """Close one queue item.

    Approving records the decision and stops there. Writing an approved
    extraction into the record would be a second, differently-shaped write
    path into the same tables, and the one in `classify` is the one under
    test — see `cli/main.py:review_resolve`, which makes the same choice.
    """
    if body.decision not in ("approved", "rejected"):
        return _err("Unknown decision.")
    with _connect() as conn:
        _repos(conn).reviews.resolve(body.review_id, body.decision)
        conn.commit()
    return _ok(f"Item {body.decision}.")


class BulkBody(BaseModel):
    reason: str
    decision: str


@router.post("/review/bulk")
def review_bulk(body: BulkBody) -> dict[str, Any]:
    """Decide a whole reason bucket at once.

    Most open items share one reason string. Clicking through them
    individually is not a workflow, it is an argument for never opening the
    queue, so the bucket is a first-class unit here.
    """
    if body.decision not in ("approved", "rejected"):
        return _err("Unknown decision.")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE review_queue SET status = %s, resolved_at = now()"
            " WHERE status = 'pending' AND reason = %s",
            (body.decision, body.reason),
        )
        conn.commit()
        count = cursor.rowcount
    return _ok(f"{count} item(s) {body.decision}.")


class RoleBody(BaseModel):
    role_title: str = ""


@router.post("/application/{application_id}/role")
def set_application_role(application_id: UUID, body: RoleBody) -> dict[str, Any]:
    """Name a role the extractor could not.

    `override_role`, not `set_role`: the latter COALESCEs, which is right for
    the extractor and would mean a human correcting a wrong title watches the
    form submit and change nothing.
    """
    with _connect() as conn:
        _repos(conn).applications.override_role(application_id, body.role_title.strip())
        conn.commit()
    return _ok("Role updated.")


class OutcomeBody(BaseModel):
    outcome: str = ""


@router.post("/application/{application_id}/close")
def close_application(application_id: UUID, body: OutcomeBody) -> dict[str, Any]:
    allowed = ("", "offer", "accepted", "rejected", "withdrawn", "declined")
    if body.outcome not in allowed:
        return _err("Unknown outcome.")
    with _connect() as conn:
        _repos(conn).applications.set_outcome(
            application_id,
            outcome=body.outcome or None,
            ended_at=datetime.now(UTC) if body.outcome else None,
        )
        conn.commit()
    if not body.outcome:
        return _ok("Application reopened.")
    return _ok(f"Application closed as {body.outcome}.")


class ContactRoleBody(BaseModel):
    company_id: UUID
    role_title: str = ""


@router.post("/contact/{contact_id}/role")
def set_contact_role(contact_id: UUID, body: ContactRoleBody) -> dict[str, Any]:
    """Record what a person was in the process.

    `contact_company.role_title` has existed since the first migration and
    nothing has ever written it.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE contact_company SET role_title = %s"
            " WHERE contact_id = %s AND company_id = %s",
            (body.role_title.strip() or None, contact_id, body.company_id),
        )
        conn.commit()
    return _ok("Role updated.")


class RenameBody(BaseModel):
    canonical_name: str
    domain: str | None = None
    kind: str | None = None


@router.post("/company/{company_id}/rename")
def rename_company(company_id: UUID, body: RenameBody) -> dict[str, Any]:
    """Fix a company's identity, keeping the old name as an alias.

    The extractor writes names like "Acme. The" and "Acmecorp" beside "AcmeCorp".
    Renaming without keeping the alias would break the next match on the old
    spelling, so `CompanyRepository.rename` records it.
    """
    with _connect() as conn:
        repos = _repos(conn)
        existing = repos.companies.get(company_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="No such company.")
        new_name = body.canonical_name.strip()
        if new_name and new_name != existing.canonical_name:
            repos.companies.ensure_alias(company_id, existing.canonical_name)
        repos.companies.rename(
            company_id,
            canonical_name=new_name or existing.canonical_name,
            domain=(body.domain or "").strip() or None,
            kind=body.kind or None,
        )
        conn.commit()
    return _ok("Company updated.")


@router.post("/company/{company_id}/negative")
def mark_company_negative(company_id: UUID) -> dict[str, Any]:
    """Confirm a company is not job-related: label its mail and drop the row.

    The same path `jobd verify --apply` takes. The audit that explains the
    decision survives, because migration 0016 made `company_verification`
    outlive the company it audited rather than cascade with it.
    """
    with _connect() as conn:
        repos = _repos(conn)
        touched = repos.messages.mark_company_negative(company_id, by="dashboard")
        repos.companies.delete(company_id)
        conn.commit()
    return _ok(f"Removed. {touched} message(s) marked not job-related.")


class ApplyRuleBody(BaseModel):
    match_type: str
    value: str
    verdict: str
    company_name: str | None = None


@router.post("/verification/apply")
def apply_suggested_rule(body: ApplyRuleBody) -> dict[str, Any]:
    """Adopt one rule an audit proposed.

    One at a time and never in bulk: `jobd verify --apply` only ever writes
    non-negative suggested rules for the same reason — a model that misread a
    chain would otherwise silence a whole correspondent on its own say-so.
    """
    with _connect() as conn:
        try:
            result = learning.learn_rule(
                conn,
                _repos(conn),
                domain=body.value if body.match_type == "domain" else None,
                address=body.value if body.match_type == "address" else None,
                verdict=body.verdict,  # type: ignore[arg-type]
                company=body.company_name or None,
            )
        except learning.LearnError as error:
            return _err(str(error))
    return _ok(f"Applied {result.match_type}={result.value}.")


class CategoryBody(BaseModel):
    category: str
    verdict: str


@router.post("/category/verdict")
def set_category_verdict(body: CategoryBody) -> dict[str, Any]:
    """Re-decide a whole named group of senders in one row.

    This is the leverage migration 0009 was written for: `LINKEDIN_DIGEST`
    tags six senders, and changing the group's verdict changes all six rather
    than re-teaching each one.
    """
    if body.verdict not in ("positive", "negative", "undecided"):
        return _err("Unknown verdict.")
    with _connect() as conn:
        conn.execute(
            "UPDATE sender_category SET verdict = %s WHERE category = %s",
            (body.verdict, body.category),
        )
        conn.commit()
    return _ok(f"{body.category} → {body.verdict}.")


# ------------------------------------------------------------ prompt library


def _prompt_json(row: Any) -> dict[str, Any]:
    return {"id": _id(row.id), "label": row.label, "text": row.text, "created_at": _dt(row.created_at)}


@router.get("/prompts")
def prompts() -> dict[str, Any]:
    """The saved-prompt library, oldest first — the same list every
    "extra instructions" picker (today: the reply box) reads from."""
    with _connect() as conn:
        return {"prompts": [_prompt_json(p) for p in _repos(conn).prompts.all()]}


class PromptBody(BaseModel):
    label: str
    text: str


@router.post("/prompts")
def create_prompt(body: PromptBody) -> dict[str, Any]:
    label, text = body.label.strip(), body.text.strip()
    if not label or not text:
        return _err("Needs both a label and text.")
    with _connect() as conn:
        try:
            row = _repos(conn).prompts.add(label, text)
        except psycopg.errors.UniqueViolation:
            return _err(f'"{label}" is already saved.')
        conn.commit()
        return {"ok": True, "prompt": _prompt_json(row)}


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: UUID) -> dict[str, Any]:
    with _connect() as conn:
        _repos(conn).prompts.delete(prompt_id)
        conn.commit()
    return _ok("Removed.")


# ------------------------------------------------------------------ chat


def _sse(event: ChatEvent) -> str:
    """One `ChatEvent` as an SSE frame.

    `type` is the event's own class name, lowercased — the client switches on
    it directly, so a new event variant in `ports/chat.py` needs no mapping
    table kept in sync here. The payload is filled by matching on the variant
    rather than by reaching for attributes the union does not have: a new
    variant then fails to type-check here instead of shipping an empty frame.
    """
    payload: dict[str, Any] = {"type": type(event).__name__.lower()}
    match event:
        case TextDelta():
            payload["text"] = event.text
        case ToolCall():
            payload["name"] = event.name
            payload["args"] = event.args
        case ToolResult():
            payload["name"] = event.name
            payload["ok"] = event.ok
            payload["summary"] = event.summary
        case ProposalEvent():
            payload["proposal"] = {
                "action": event.proposal.action,
                "summary": event.proposal.summary,
                "endpoint": event.proposal.endpoint,
                "fields": event.proposal.fields,
                "blast_radius": event.proposal.blast_radius,
            }
        case Error():
            payload["message"] = event.message
        case Done():
            payload["reason"] = event.reason
    return f"data: {json.dumps(payload)}\n\n"


class ChatBody(BaseModel):
    message: str
    history: list[dict[str, Any]] = []
    url_context: str = ""
    #: Which model this turn uses — the panel's picker. Checked against
    #: `_chat_models()`, not passed to `load_provider` unvalidated: a plain
    #: string in a POST body is the whole access-control surface for "what
    #: can the chat send my mail to" (see `_CHAT_MODEL_CHOICES`), so an
    #: unrecognised value falls back to the operator's own default rather
    #: than being honoured.
    model: str | None = None


@router.post("/chat")
def chat_turn(request: Request, body: ChatBody) -> StreamingResponse:
    """One chat turn, streamed as SSE.

    Off unless `JOBD_CHAT_MODEL` is set — a 404, not a 500, so the panel's
    absence from the shell and this route's absence agree with each other
    (docs/chat-and-summaries.md §10 gate 8).

    `history` is state the client holds and sends, never a server-side store:
    the conversation lives in the page and nowhere else (§6, "Conversations
    are ephemeral"). Message bodies a prior tool call retrieved are never part
    of it, only role and text.
    """
    del request
    settings = load_settings()
    if not settings.chat_model:
        raise HTTPException(status_code=404, detail="Chat is not configured.")

    model = body.model if body.model in _chat_models() else settings.chat_model
    # Web search on by default (per-turn `:online` suffix, same OpenRouter
    # mechanism the summary route already uses on request) — every chat turn
    # now grounds in real web results, not just company-summary generation.
    if not model.endswith(":online"):
        model = f"{model}:online"

    past = [
        Turn(role=h["role"], text=str(h.get("text", "")))
        for h in body.history
        if isinstance(h, dict) and h.get("role") in ("user", "assistant")
    ]

    from jobd.adapters.llm import load_provider

    provider = load_provider(model)
    if not hasattr(provider, "converse"):
        raise HTTPException(
            status_code=400,
            detail=f"{model!r} has no chat capability "
            "(the ollama provider implements extract() only).",
        )

    message_text = body.message

    def stream() -> Iterator[str]:
        conn = _connect()
        try:
            for event in chat_service.converse_turn(
                conn=conn,
                provider=cast(Any, provider),
                message=message_text,
                history=past,
                url_context=body.url_context or None,
                self_address=_self_address(conn),
            ):
                yield _sse(event)
        except Exception as exc:  # never let a mid-stream crash hang the panel
            from jobd.ports.chat import Error

            yield _sse(Error(str(exc)))
        finally:
            conn.rollback()  # the connection is read-only; nothing to commit
            conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream")


# ------------------------------------------------------------------- scrape


class ScrapeStartBody(BaseModel):
    accounts: list[str] = []
    window_days: int | None = None
    model: str = "default"


#: The whole-pipeline runner behind the Scrape page's "Full pipeline" card.
#: Steps run as the CLI subprocesses they already are — same code, same
#: metrics rows on the Runs page, same credit guard — sequenced on one
#: worker thread. Single-flight, like the scrape manager.
_PIPELINE: dict[str, Any] = {"running": False, "step": None, "steps": [],
                             "started_at": None, "finished_at": None,
                             "results": [], "error": None}
_PIPELINE_LOCK = threading.Lock()

_PIPELINE_STEPS = ("scrape", "sweep", "distill", "audit")


class PipelineRunBody(BaseModel):
    steps: list[str]
    window_days: int = 3


def _jobd_argv() -> list[str]:
    """The venv's own `jobd` entry point, without guessing install paths."""
    import sys

    script = Path(sys.executable).parent / "jobd"
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-c", "from jobd.cli.main import main; main()"]


def _pipeline_worker(steps: list[str], window_days: int) -> None:
    import subprocess

    judge = os.environ.get(
        "JOBD_JUDGE_MODEL", "openrouter/google/gemini-3.7-flash"
    )
    commands: dict[str, list[str]] = {
        "scrape": ["scrape", "--window", str(window_days)],
        "sweep": ["review", "sweep"],
        "distill": ["distill", "--apply"],
        "audit": ["audit", "--model", judge, "--judge-model", judge,
                   "--apply", "--workers", "8"],
    }
    try:
        for step in steps:
            _PIPELINE["step"] = step
            proc = subprocess.run(
                _jobd_argv() + commands[step],
                capture_output=True, text=True, timeout=7200,
            )
            tail = (proc.stdout + proc.stderr)[-2000:]
            _PIPELINE["results"].append(
                {"step": step, "ok": proc.returncode == 0, "tail": tail}
            )
            if proc.returncode != 0:
                _PIPELINE["error"] = f"{step} exited {proc.returncode}"
                break
    except Exception as exc:  # the card shows the failure; nothing crashes
        _PIPELINE["error"] = f"{_PIPELINE['step']}: {exc}"
    finally:
        _PIPELINE["running"] = False
        _PIPELINE["step"] = None
        _PIPELINE["finished_at"] = datetime.now(UTC).isoformat()


@router.post("/pipeline/run")
def pipeline_run(body: PipelineRunBody) -> dict[str, Any]:
    """Run selected pipeline steps over a trailing window, in order.

    The steps are the daily loop's own stages (`just daily` generalized):
    scrape --window N, review sweep, distill --apply, full audit --apply.
    """
    steps = [s for s in _PIPELINE_STEPS if s in body.steps]
    if not steps:
        return _err(f"No valid steps. Choose from: {', '.join(_PIPELINE_STEPS)}")
    if not 1 <= body.window_days <= 365:
        return _err("window_days must be between 1 and 365.")
    with _PIPELINE_LOCK:
        if _PIPELINE["running"]:
            return {"ok": True, "started": False, **_pipeline_status()}
        _PIPELINE.update(
            running=True, step=None, steps=steps, results=[], error=None,
            started_at=datetime.now(UTC).isoformat(), finished_at=None,
        )
    threading.Thread(
        target=_pipeline_worker, args=(steps, body.window_days), daemon=True
    ).start()
    return {"ok": True, "started": True, **_pipeline_status()}


def _pipeline_status() -> dict[str, Any]:
    return {
        "running": _PIPELINE["running"],
        "step": _PIPELINE["step"],
        "steps": _PIPELINE["steps"],
        "started_at": _PIPELINE["started_at"],
        "finished_at": _PIPELINE["finished_at"],
        "results": _PIPELINE["results"],
        "error": _PIPELINE["error"],
    }


@router.get("/pipeline/status")
def pipeline_status() -> dict[str, Any]:
    return _pipeline_status()


@router.post("/scrape/start")
def scrape_start(body: ScrapeStartBody) -> dict[str, Any]:
    """Kick off a seed-and-expand run on a worker thread.

    Single-flight: a second POST while one is running reports the running
    one rather than racing it. Accounts default to whatever the record
    already knows plus the token store — the common case is 'scrape my
    mailboxes again', not naming them.
    """
    from jobd.scrape.manager import MANAGER

    accounts = body.accounts
    if not accounts:
        # The record itself is the best default: every mailbox that has ever
        # been ingested. (The token store's listing is keyring-dependent and
        # unreliable — see GmailSource.accounts' docstring.)
        with _connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT account FROM message WHERE channel = 'email'"
            ).fetchall()
        accounts = [str(r[0]) for r in rows]
    if not accounts:
        return _err("No accounts connected. Run `jobd auth gmail` first.")
    from jobd.services.demo import DEMO_ACCOUNT

    if all(a == DEMO_ACCOUNT for a in accounts):
        # The demo corpus is mail that never existed — a sync would march
        # straight into Gmail auth and budget refusals and land as a scary
        # "Last run failed" on a record that is working exactly as intended.
        return _err(
            "This record is the synthetic demo corpus — there is no Gmail "
            "behind it to sync. Connect a real mailbox first (docs/tour.md)."
        )
    # Synchronous preflight so a dead AWS session answers the click itself,
    # not an error banner half a minute into a doomed run.
    from jobd.scrape.service import ScrapeConfigError, check_raw_storage

    try:
        check_raw_storage()
    except ScrapeConfigError as exc:
        return _err(str(exc))
    started = MANAGER.start(
        accounts=accounts, window_days=body.window_days, model=body.model
    )
    return {"ok": True, "started": started, **MANAGER.status()}


@router.get("/scrape/status")
def scrape_status() -> dict[str, Any]:
    from jobd.scrape.manager import MANAGER

    return MANAGER.status()


@router.get("/scrape/stream")
def scrape_stream(after: int = 0) -> StreamingResponse:
    """The run's event feed as SSE, resumable via `after` (last seen seq).

    Long-polls the emitter's ring buffer; 15s of silence becomes a comment
    heartbeat so proxies keep the stream open. Ends after the terminal
    `done` event so the client's EventSource can close cleanly.
    """
    from jobd.scrape.manager import MANAGER

    emitter = MANAGER.emitter

    def stream() -> Iterator[str]:
        if emitter is None:
            yield 'data: {"kind": "idle"}\n\n'
            return
        seq = after
        while True:
            events = emitter.wait(seq, timeout=15.0)
            if not events:
                if not MANAGER.running:
                    return
                yield ": heartbeat\n\n"
                continue
            for event in events:
                seq = event.seq
                payload = {"kind": event.kind, "seq": event.seq, "ts": event.ts}
                payload.update(event.data)
                yield f"data: {json.dumps(payload)}\n\n"
                if event.kind == "done":
                    return

    return StreamingResponse(stream(), media_type="text/event-stream")


# ----------------------------------------------------------- linkedin push


class LinkedInMessageIn(BaseModel):
    """One message as the companion extension reports it (adapters/linkedin)."""

    external_id: str
    conversation_id: str
    direction: str
    partner_id: str
    partner_name: str = ""
    text: str
    sent_at: datetime


class LinkedInPushIn(BaseModel):
    #: The logged-in member's public slug — which LinkedIn inbox this is.
    account: str
    messages: list[LinkedInMessageIn] = []


@router.post("/linkedin/push")
def linkedin_push(body: LinkedInPushIn, request: Request) -> dict[str, Any]:
    """Receive a batch of LinkedIn DMs from the companion extension.

    The one write endpoint a browser extension calls, so the one place the
    same-origin middleware cannot vouch for (`app.py` exempts this path).
    Its replacement is a bearer token: `JOBD_LINKEDIN_TOKEN` in the server's
    environment, pasted once into the extension popup. Constant-time compare;
    503 when unconfigured, so the feature is absent rather than open.

    Idempotent end to end (I2): the adapter renders deterministic bytes, keys
    are content hashes, storage is write-once — the extension may re-push its
    whole watermark window after a crash and the second pass is a no-op.
    """
    import hmac as _hmac

    from jobd.adapters.linkedin import PushedMessage, to_raw_message
    from jobd.services import ingest as ingest_service

    expected = os.environ.get("JOBD_LINKEDIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="JOBD_LINKEDIN_TOKEN is unset — LinkedIn push isn't configured.",
        )
    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ").strip()
    if not _hmac.compare_digest(supplied.encode(), expected.strip().encode()):
        raise HTTPException(status_code=401, detail="Bad or missing bearer token.")

    raws = []
    errors: list[str] = []
    for m in body.messages:
        try:
            raws.append(
                to_raw_message(
                    PushedMessage(
                        external_id=m.external_id,
                        conversation_id=m.conversation_id,
                        direction=m.direction,
                        partner_id=m.partner_id,
                        partner_name=m.partner_name,
                        text=m.text,
                        sent_at=m.sent_at,
                    ),
                    account_slug=body.account,
                )
            )
        except Exception as exc:  # one bad message must not drop the batch
            errors.append(f"{m.external_id}: {type(exc).__name__}: {exc}")

    from jobd.adapters.linkedin.push import account_address

    storage = _storage()
    with _connect() as conn:
        result = ingest_service.ingest_raw(
            raws,
            storage=storage,
            messages=_repos(conn).messages,
            channel="linkedin",
            account=account_address(body.account),
        )
        conn.commit()

    return {
        "received": len(body.messages),
        "stored": result.stored,
        "already_stored": result.already_stored,
        "rows_inserted": result.rows_inserted,
        "rows_existing": result.rows_existing,
        "errors": errors + result.errors,
    }


@router.get("/scrape/runs")
def scrape_runs() -> dict[str, Any]:
    """Run history, newest first — what last night's cron actually did."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, started_at, finished_at, accounts, window_days, counters"
            " FROM scrape_run ORDER BY started_at DESC LIMIT 30"
        ).fetchall()
    return {
        "runs": [
            {
                "id": _id(row[0]),
                "started_at": _dt(row[1]),
                "finished_at": _dt(row[2]),
                "accounts": list(row[3]),
                "window_days": row[4],
                "counters": row[5],
            }
            for row in rows
        ]
    }
