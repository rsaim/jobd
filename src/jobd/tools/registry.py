"""The chat panel's tool set — read tools over `services/dashboard.py` and
`services/timeline.py`, propose tools over the write surface `web/app.py`
already ships.

One registry, because the sidebar chat and any future MCP server (M9 proper)
are two transports over the same thing, not two implementations of it —
docs/chat-and-summaries.md §1. A read tool's handler runs the exact function
the dashboard route already calls (`get_communications` *is*
`dashboard.list_communications`, not a lookalike — M7 gate 2). A propose
tool's handler never touches the database at all: it returns a `Proposal`
naming an *existing* POST endpoint and its pre-filled fields, so applying it
is a human clicking the same form the corresponding page already renders.
That is the whole mutation story — see `ports.chat.Proposal`'s docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable, Literal
from uuid import UUID

import psycopg

from jobd.ports.chat import Proposal
from jobd.services import dashboard, learning, timeline
from jobd.services.timeline import find_company


@dataclass(frozen=True, slots=True)
class Tool:
    """One callable the model may invoke.

    `kind` has two members and no `"write"` — documentation and a test, not a
    proof (a `"read"` handler could still contain an `UPDATE`; what actually
    stops that is every handler here running against a connection with
    `SET TRANSACTION READ ONLY` already issued, in `services/chat.py`). A
    `"propose"` handler is additionally trusted, by construction, never to
    import a repository or hold a writable connection — see the handlers
    below; none of them accept `conn`.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    kind: Literal["read", "propose"]


def _jsonable(value: Any) -> Any:
    """A raw SQL row still carries UUIDs/datetimes/Decimals — make it
    something a `ChatEvent` can actually be serialised as."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"{value!r} is not a valid id. Call a read tool "
            "(list_companies, company_timeline, ...) in THIS turn to get "
            "the real id first — an id from an earlier turn is not carried "
            "forward, only the text of what was said."
        ) from None


def build_tools(
    *,
    conn: psycopg.Connection[Any],
    self_address: str | None,
    max_messages: int,
    max_chars: int,
    embed: Callable[[str], list[float]] | None = None,
) -> list[Tool]:
    """The registry, bound to one request's read-only connection.

    Rebuilt per chat turn rather than held globally: `conn` is that turn's
    read-only transaction (`services/chat.py`), and closing over it here is
    what lets every read handler below skip repeating "which connection" —
    there is only ever one, and it cannot write.

    `embed`: text -> vector, for `search_communications(mode="semantic")`.
    `None` (tests, or a future non-embedding provider) makes that mode
    return a clear error instead of crashing — see the handler below.
    """

    def list_companies(
        search: str | None = None, kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = dashboard.list_companies(
            conn, search=search or None, kind=kind or None, limit=min(limit, 50)
        )
        return [
            {
                "id": str(r.id),
                "name": r.canonical_name,
                "domain": r.domain,
                "kind": r.kind,
                "applications": r.application_count,
                "last_touch": r.last_touch.isoformat() if r.last_touch else None,
            }
            for r in rows
        ]

    def resolve_company(company: str) -> UUID:
        """UUID, domain, alias, or canonical name — whatever the model has
        on hand, resolved the same way the CLI's `--company` flag and
        `/company/{id}` both already do. Shared by every tool below that
        takes a company: a propose tool's `company_id` argument routinely
        arrives as a *name* rather than an id, because tool results from an
        earlier turn are deliberately not carried forward (§4) — the model
        only remembers what it said, not what a tool returned two turns
        ago. Falling back to a name lookup here is what keeps that a minor
        inconvenience instead of a crash.
        """
        try:
            return UUID(str(company))
        except (ValueError, AttributeError, TypeError):
            pass
        found = find_company(conn, company)
        if found is None:
            raise ValueError(
                f"No company matching {company!r}. Call list_companies or "
                "company_timeline first to find the right one."
            )
        return found

    def company_timeline(company: str) -> dict[str, Any]:
        try:
            company_id = resolve_company(company)
        except ValueError as exc:
            return {"error": str(exc)}
        t = timeline.build(conn, company_id)
        return {
            "company_id": str(t.company_id),
            "name": t.canonical_name,
            "kind": t.kind,
            "applications": [
                {
                    "application_id": str(a.application_id),
                    "role_title": a.role_title,
                    "outcome": a.outcome,
                    "latest_stage": a.latest_stage,
                    "started_at": a.started_at.isoformat(),
                    "ghosted": a.ghosted(now=datetime.now(UTC)),
                }
                for a in t.applications
            ],
        }

    def search_communications(
        search: str | None = None,
        company: str | None = None,
        mode: str = "keyword",
        limit: int = 8,
    ) -> dict[str, Any]:
        company_id = resolve_company(company) if company else None
        capped = min(limit, max_messages)
        if mode == "semantic":
            if not search:
                return {"error": "mode=\"semantic\" needs `search` (the query text)."}
            if embed is None:
                return {"error": "Semantic search isn't available for this model."}
            vector = embed(search)
            rows = dashboard.semantic_search_communications(
                conn, vector, company_id=company_id, limit=capped
            )
        else:
            rows = dashboard.list_communications(
                conn, company_id=company_id, search=search or None, limit=capped
            )
        return {
            "mode": mode,
            "truncated_to": capped,
            "messages": [
                {
                    "id": str(r.id),
                    "subject": r.subject,
                    "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                    "direction": r.direction,
                    "from_contact": r.contact_name or r.contact_address,
                }
                for r in rows
            ],
        }

    def message_detail(message_id: str) -> dict[str, Any]:
        detail = dashboard.message_detail(conn, _uuid(message_id))
        if detail is None:
            return {"error": "No such message."}
        body = detail.body_text[:max_chars]
        return {
            "id": str(detail.id),
            "subject": detail.subject,
            "sent_at": detail.sent_at.isoformat() if detail.sent_at else None,
            "direction": detail.direction,
            "company": detail.company_name,
            # The real reply-to address, not a display name — propose_draft_reply's
            # `to` needs an actual address (found live: a model given only
            # contact_name drafted `to: "Natalia Knop"`, which is not an
            # email address and would fail at Gmail).
            "sender_address": detail.sender_address,
            "contact_name": detail.contact_name,
            "contact_address": detail.contact_address,
            "body": body,
            "truncated": len(detail.body_text) > max_chars,
        }

    def compute_stats() -> dict[str, Any]:
        s = dashboard.compute_stats(conn)
        return {
            "total_applications": s.total_applications,
            "by_outcome": s.by_outcome,
            "response_rate": round(s.response_rate, 3),
            "ghost_rate": round(s.ghost_rate, 3),
            "interviews": s.interviews,
            "offers": s.offers,
            "rejections": s.rejections,
        }

    def list_rules(
        verdict: str | None = None, source: str | None = None
    ) -> list[dict[str, Any]]:
        rules = dashboard.list_rules(conn, source=source or None, verdict=verdict or None)
        return [
            {
                "match_type": r["match_type"],
                "value": r["value"],
                "verdict": r["verdict"],
                "company": r["company_name"],
                "source": r["source"],
            }
            for r in rules[:30]
        ]

    def pending_reviews(limit: int = 10) -> list[dict[str, Any]]:
        rows = dashboard.pending_reviews(conn, limit=min(limit, 20))
        return [
            {
                "id": str(r["id"]),
                "reason": r["reason"],
                "subject": r.get("subject"),
            }
            for r in rows
        ]

    def run_sql(query: str) -> dict[str, Any]:
        """Ad-hoc SQL for questions the curated tools above don't cover.

        Safe by construction, not by this function's own judgment: `conn`
        already has `SET TRANSACTION READ ONLY` issued on it in
        `services/chat.py` before this registry is even built, so a write
        here fails at the database, exactly as gate 1 tests. The statement
        prefix check below is a better error message for the model, not the
        actual defence — Postgres is.
        """
        stripped = query.strip().lstrip("(")
        if not stripped[:1].lower() in ("s", "w"):  # SELECT / WITH ... SELECT
            return {"error": "Only SELECT queries are answerable here."}
        try:
            cursor = conn.execute(
                query if " limit " in query.lower() else f"{query.rstrip(';')} LIMIT 50"
            )
            cols = [d.name for d in cursor.description or []]
            rows = cursor.fetchmany(50)
        except Exception as exc:  # a bad query is a ToolResult(ok=False), not a crash
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"columns": cols, "rows": [[_jsonable(v) for v in r] for r in rows]}

    def propose_draft_reply(
        message_id: str, to: str, subject: str, body: str
    ) -> Proposal:
        """Draft, never send — the endpoint this posts to
        (`/message/<id>/reply/draft`) writes to the account's own Gmail
        Drafts folder and records it (migration 0019); actually sending it
        is a second, separate confirmed click the panel offers once this
        one is applied, never something this tool can reach on its own
        (SECURITY.md §4, I1)."""
        mid = _uuid(message_id)
        return Proposal(
            action="draft_reply",
            summary=f"Draft a reply to {to}: {subject!r}",
            endpoint=f"/message/{mid}/reply/draft",
            fields={"to": to, "subject": subject, "body": body},
        )

    def propose_teach_rule(
        verdict: str,
        domain: str | None = None,
        address: str | None = None,
        company: str | None = None,
        company_domain: str | None = None,
    ) -> Proposal:
        blast = None
        try:
            preview = learning.preview_fanout(
                _ReposShim(messages=_fanout_repo(conn)),
                domain=domain or None,
                address=address or None,
            )
            blast = (
                f"resolves {preview.backlog} backlog message(s),"
                f" {preview.already_classified} already classified"
            )
        except Exception:
            blast = None
        target = domain or address or "?"
        return Proposal(
            action="teach_rule",
            summary=f"Mark mail from {target} as {verdict}"
            + (f" ({company})" if company else ""),
            endpoint="/teach",
            fields={
                k: v
                for k, v in {
                    "domain": domain,
                    "address": address,
                    "verdict": verdict,
                    "company": company,
                    "company_domain": company_domain,
                    "next": "/triage?tab=rules",
                }.items()
                if v
            },
            blast_radius=blast,
        )

    def propose_set_application_outcome(
        application_id: str, outcome: str
    ) -> Proposal:
        return Proposal(
            action="set_application_outcome",
            summary=f"Set this application's outcome to {outcome or 'open'}",
            endpoint=f"/application/{_uuid(application_id)}/close",
            fields={"outcome": outcome, "next": "/"},
        )

    def propose_rename_company(
        company_id: str,
        canonical_name: str,
        domain: str | None = None,
        kind: str | None = None,
    ) -> Proposal:
        return Proposal(
            action="rename_company",
            summary=f"Rename this company to {canonical_name!r}"
            + (f", kind={kind}" if kind else ""),
            endpoint=f"/company/{resolve_company(company_id)}/rename",
            fields={
                k: v
                for k, v in {
                    "canonical_name": canonical_name,
                    "domain": domain,
                    "kind": kind,
                }.items()
                if v
            },
        )

    def propose_mark_company_negative(company_id: str, reason: str) -> Proposal:
        cid = resolve_company(company_id)
        return Proposal(
            action="mark_company_negative",
            summary=(
                f"Mark this company NOT job-related — every linked message"
                f" gets labelled negative and the company row is deleted."
                f" Reason: {reason}"
            ),
            endpoint=f"/company/{cid}/negative",
            fields={},
        )

    def propose_resolve_review(review_id: str, decision: str) -> Proposal:
        return Proposal(
            action="resolve_review",
            summary=f"{decision.capitalize()} this review item",
            endpoint="/review/resolve",
            fields={
                "review_id": review_id,
                "decision": decision,
                "next": "/triage",
            },
        )

    read = [
        Tool(
            name="list_companies",
            description="Search/list companies with application counts and last touch.",
            parameters={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "kind": {"type": "string", "enum": ["employer", "agency"]},
                    "limit": {"type": "integer"},
                },
            },
            handler=list_companies,
            kind="read",
        ),
        Tool(
            name="company_timeline",
            description="One company's applications, stages, and outcomes.",
            parameters={
                "type": "object",
                "required": ["company"],
                "properties": {"company": {"type": "string"}},
            },
            handler=company_timeline,
            kind="read",
        ),
        Tool(
            name="search_communications",
            description=(
                "Search messages, optionally scoped to a company. Two modes: "
                "\"keyword\" (default) is exact-word/phrase full-text match — "
                "fast, precise, use it when the query has specific terms "
                "(a name, a job title, an exact phrase). \"semantic\" embeds "
                "the query and finds messages *about* the same thing even "
                "when they share none of its words — use it for a vaguer "
                "question (\"anything about salary negotiation\", \"who "
                "mentioned relocation\"). semantic only searches messages "
                "that have been embedded (not the whole mailbox — see "
                "`jobd embed-backfill`); if it comes back empty, retry with "
                "\"keyword\" before concluding nothing exists."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "company": {"type": "string"},
                    "mode": {"type": "string", "enum": ["keyword", "semantic"]},
                    "limit": {"type": "integer"},
                },
            },
            handler=search_communications,
            kind="read",
        ),
        Tool(
            name="message_detail",
            description="One message's body and metadata, truncated to a safe length.",
            parameters={
                "type": "object",
                "required": ["message_id"],
                "properties": {"message_id": {"type": "string"}},
            },
            handler=message_detail,
            kind="read",
        ),
        Tool(
            name="compute_stats",
            description="Applications, response/ghost rate, interviews, offers, rejections.",
            parameters={"type": "object", "properties": {}},
            handler=compute_stats,
            kind="read",
        ),
        Tool(
            name="list_rules",
            description="Deterministic sender_rule entries, optionally filtered.",
            parameters={
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["positive", "negative", "undecided"]},
                    "source": {"type": "string", "enum": ["human", "auto"]},
                },
            },
            handler=list_rules,
            kind="read",
        ),
        Tool(
            name="pending_reviews",
            description="Review-queue items awaiting a human decision.",
            parameters={"type": "object", "properties": {"limit": {"type": "integer"}}},
            handler=pending_reviews,
            kind="read",
        ),
        Tool(
            name="run_sql",
            description=(
                "Ad-hoc read-only SQL for a question none of the tools above answer. "
                "Runs on the same read-only connection as everything else here — a "
                "write is refused by the database, not by this description. Core "
                "tables: message (company_id, application_id, contact_id, "
                "classified_by, sent_at, direction, subject, body_text), "
                "company (canonical_name, domain, kind: employer|agency), "
                "application (company_id, role_title, outcome, started_at), "
                "stage_event (application_id, stage, occurred_at — stages: applied, "
                "recruiter_screen, phone_screen, technical, onsite, offer, rejected, "
                "withdrawn, accepted, declined), contact (display_name), "
                "review_queue (status, reason). Capped at 50 rows; add your own "
                "LIMIT for fewer."
            ),
            parameters={
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
            handler=run_sql,
            kind="read",
        ),
    ]

    propose = [
        Tool(
            name="propose_draft_reply",
            description=(
                "Draft a reply to a message for the user to review and send "
                "themselves. Call message_detail first to get a real message_id "
                "and, critically, a real reply-to email address (its "
                "sender_address/contact_address fields) — never a display name, "
                "and never invented. Never sends anything; drafting is the only "
                "thing this can do."
            ),
            parameters={
                "type": "object",
                "required": ["message_id", "to", "subject", "body"],
                "properties": {
                    "message_id": {"type": "string"},
                    "to": {
                        "type": "string",
                        "description": "A real email address from message_detail "
                        "(sender_address or contact_address) — not a name. "
                        "Comma-separated if more than one.",
                    },
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
            handler=propose_draft_reply,
            kind="propose",
        ),
        Tool(
            name="propose_teach_rule",
            description=(
                "Draft a sender_rule change (positive/negative/undecided) for the"
                " user to confirm. Never applied by this call."
            ),
            parameters={
                "type": "object",
                "required": ["verdict"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["positive", "negative", "undecided"]},
                    "domain": {"type": "string"},
                    "address": {"type": "string"},
                    "company": {"type": "string"},
                    "company_domain": {"type": "string"},
                },
            },
            handler=propose_teach_rule,
            kind="propose",
        ),
        Tool(
            name="propose_set_application_outcome",
            description=(
                "Draft closing an application with an outcome for the user to"
                " confirm. offer/accepted/rejected/withdrawn/declined, or empty"
                " to reopen."
            ),
            parameters={
                "type": "object",
                "required": ["application_id", "outcome"],
                "properties": {
                    "application_id": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": ["", "offer", "accepted", "rejected", "withdrawn", "declined"],
                    },
                },
            },
            handler=propose_set_application_outcome,
            kind="propose",
        ),
        Tool(
            name="propose_rename_company",
            description="Draft correcting a company's name/domain/kind for the user to confirm.",
            parameters={
                "type": "object",
                "required": ["company_id", "canonical_name"],
                "properties": {
                    "company_id": {
                        "type": "string",
                        "description": "A company's id, domain, or exact name — "
                        "whatever you have on hand.",
                    },
                    "canonical_name": {"type": "string"},
                    "domain": {"type": "string"},
                    "kind": {"type": "string", "enum": ["employer", "agency"]},
                },
            },
            handler=propose_rename_company,
            kind="propose",
        ),
        Tool(
            name="propose_mark_company_negative",
            description=(
                "Draft marking a company not-job-related: every linked message"
                " is labelled negative and the company row is deleted. Use only"
                " when confident — this is the strongest action available."
            ),
            parameters={
                "type": "object",
                "required": ["company_id", "reason"],
                "properties": {
                    "company_id": {
                        "type": "string",
                        "description": "A company's id, domain, or exact name — "
                        "whatever you have on hand.",
                    },
                    "reason": {"type": "string"},
                },
            },
            handler=propose_mark_company_negative,
            kind="propose",
        ),
        Tool(
            name="propose_resolve_review",
            description="Draft approving or rejecting one review-queue item.",
            parameters={
                "type": "object",
                "required": ["review_id", "decision"],
                "properties": {
                    "review_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["approved", "rejected"]},
                },
            },
            handler=propose_resolve_review,
            kind="propose",
        ),
    ]

    return read + propose


@dataclass(frozen=True, slots=True)
class _ReposShim:
    """`learning.preview_fanout` wants `repos.messages.count_fanout` — the
    read-only path has no repository layer of its own (it reads through
    `dashboard.py`, not `repositories.py` — see that module's own docstring
    on why), so this adapts the one method it needs onto the same
    read-only `conn` rather than pulling in a full `Repositories`."""

    messages: Any


def _fanout_repo(conn: psycopg.Connection[Any]) -> Any:
    from jobd.adapters.postgres.repositories import MessageRepository

    return MessageRepository(conn)
