"""The seed-and-expand scrape as a LangGraph state machine.

::

    plan ─→ list ─→ fetch ─→ classify ─→ harvest ─┬─→ list      (frontier left)
                                                  ├─→ residual ─→ list
                                                  └─→ report

The cycle *is* the algorithm: positives found by one pass teach the entities
(sender domains, addresses, threads) whose queries drive the next. Measured on
the reference mailbox this converges in 1-2 hops at ~97% recall for ~20% of
the mailbox fetched; the residual pass — direct-addressed mail outside the
noise categories — held every remaining positive (docs/seed-and-expand.md).

Why a graph framework and not a for-loop: each node is an idempotent,
restartable unit (re-running any of them against the DB is a no-op for work
already done — v1's I2/I3 invariants do the heavy lifting), the expansion
cycle is an explicit conditional edge rather than control flow buried in a
function, and every node reports through one event stream the CLI and the
live dashboard both consume. State stays small and serializable
(state.ScrapeState); the heavy clients ride in config (state.RunContext).

Everything mailbox-touching is Gmail-shaped but nothing here imports the
adapter: nodes call ``ctx.source`` duck-typed (search_ids / thread_message_ids
/ get_one), so a future IMAP or LinkedIn source slots in at the composition
root (service.py) without touching the graph.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from jobd.domain.resolve import GENERIC_DOMAINS
from jobd.scrape import queries as q
from jobd.scrape.audit import run_audit
from jobd.scrape.facts import mailbox_facts, new_company_facts, stage_facts
from jobd.scrape.queries import ATS_DOMAINS
from jobd.scrape.state import Entity, Query, RunContext, ScrapeState
from jobd.services.classify import classify_pending
from jobd.services.ingest import IngestResult
from jobd.services.ingest import _one as ingest_one

#: Expansion stops after this many hops even if entities keep trickling in —
#: the experiments converged in 1-2; anything past 4 is pathology, not signal.
MAX_HOPS = 4

#: Messages fetched per commit. One batch lost is one batch re-fetched.
FETCH_BATCH = 100

#: Messages classified per service call; same batching contract as v1's CLI.
CLASSIFY_BATCH = 300


def _llm_workers() -> int:
    """Concurrent extraction calls per batch, for the scrape's classify leg.

    The CLI default stays 1 (`--llm-workers`: rate-limited cheap tiers stall
    on parallel bursts), but the dashboard scrape defaults to 4: the models
    this codebase actually configures (OpenRouter paid tier) take a small
    burst fine, and a backlog batch at one sequential call every ~5s is half
    an hour of dead air behind a Sync button. JOBD_LLM_WORKERS overrides in
    either direction.
    """
    try:
        return max(1, int(os.environ.get("JOBD_LLM_WORKERS", "4")))
    except ValueError:
        return 4


class _LiveMeter:
    """Progress tee for the scrape's classify leg.

    `classify_pending` reports through the meter protocol; this forwards
    everything to a real `RunMeter` — so the Runs page gets the same live
    row a CLI classify writes (items, rate, ETA, spend, and the pending
    mail's date range in ``args``) — and mirrors the phase-2 per-call ticks onto
    the SSE stream as throttled ``classify_progress`` frames, tokens and
    cost read live off the provider's own counters.

    Exists because of a live catch: a 2,500-message backlog put ~30 silent
    minutes between two stream events (phase 2 of the first batch emits
    nothing), and from the dashboard that was indistinguishable from a hang.
    """

    #: Seconds between stream frames — matches RunMeter.FLUSH_SECONDS, and
    #: keeps a multi-hour drain to a few thousand events in the ring buffer.
    EMIT_SECONDS = 2.0

    def __init__(self, inner: Any, emit: Any, llm: Any, hop: int) -> None:
        self._inner = inner
        self._emit = emit
        self._llm = llm
        self._hop = hop
        self._last = 0.0
        self._ticks: dict[str, Any] = {}

    def bump(self, outcome: str | None = None, n: int = 1) -> None:
        self._inner.bump(outcome, n)

    def error(self, message: str) -> None:
        self._inner.error(message)

    def set_total(self, total: int | None) -> None:
        self._inner.set_total(total)

    def flush(self, force: bool = False) -> None:
        self._inner.flush(force)

    def finish(self, status: str = "done") -> None:
        self._inner.finish(status)

    def set_counter(self, key: str, value: Any) -> None:
        self._inner.set_counter(key, value)
        self._ticks[key] = value
        now = time.monotonic()
        if now - self._last < self.EMIT_SECONDS:
            return
        self._last = now
        llm = self._llm
        self._emit.emit(
            "classify_progress",
            hop=self._hop,
            llm_done=int(self._ticks.get("llm_done", 0) or 0),
            llm_total=int(self._ticks.get("llm_total", 0) or 0),
            llm_calls=int(getattr(llm, "calls", 0) or 0),
            prompt_tokens=float(getattr(llm, "prompt_tokens", 0) or 0),
            completion_tokens=float(getattr(llm, "completion_tokens", 0) or 0),
            cost_usd=round(float(getattr(llm, "cost_usd", 0.0) or 0.0), 4),
        )


def _live_meter(ctx: RunContext, state: ScrapeState) -> _LiveMeter:
    """One pipeline_run row per classify leg, bridged to the scrape stream.

    ``args`` carries the sent-date range of the pending mail
    (``mail_since``/``mail_until``) so the Runs page says which dates this
    leg is *classifying* — NOT the sync's search window. The two diverge
    exactly when it matters: the leg drains everything unclassified, and a
    1-day sync that inherits a months-old backlog would otherwise wear a
    "mail Sep 14 → Sep 15" label while chewing through February (the
    live-caught confusion that added this). The search window itself is the
    scrape_run row's job. Total is the same pending count — the backlog
    this leg will drain, since `classify` loops until nothing is pending.
    """
    from jobd.services.metrics import RunMeter

    args: dict[str, Any] = {
        "source": "scrape",
        "model": getattr(ctx.llm, "name", "?"),
        "window_days": state["window_days"],
    }
    remaining = ctx.conn.execute(
        "SELECT count(*), min(sent_at)::date, max(sent_at)::date"
        " FROM message WHERE classified_at IS NULL"
    ).fetchone()
    if remaining and remaining[1] is not None:
        args["mail_since"] = remaining[1].isoformat()
        args["mail_until"] = remaining[2].isoformat()
    inner = RunMeter.start(
        kind="classify",
        total=int(remaining[0]) if remaining else None,
        args=args,
        worker="scrape",
        providers=[p for p in (ctx.llm, ctx.judge) if p is not None],
    )
    return _LiveMeter(inner, ctx.emit, ctx.llm, state["hop"])


def _ctx(config: RunnableConfig) -> RunContext:
    ctx: RunContext = config["configurable"]["ctx"]
    return ctx


def _is_excluded_domain(domain: str) -> bool:
    # Shared providers would pull the whole mailbox; ATS platforms are
    # already seed queries, so expanding into one is a duplicate. Banks and
    # other noise never get here any more: a learned negative rule means
    # their messages classify negative and never mint an entity.
    pool = GENERIC_DOMAINS | ATS_DOMAINS
    return any(domain == d or domain.endswith("." + d) for d in pool)


# ------------------------------------------------------------------- nodes


def plan(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Load the skip set, build the opening frontier, announce the run."""
    ctx = _ctx(config)
    window = state.get("window_days")
    frontier: list[Query] = []
    for account in state["accounts"]:
        rows = ctx.conn.execute(
            "SELECT external_id FROM message"
            " WHERE channel = 'email' AND account = %s AND external_id IS NOT NULL",
            (account,),
        ).fetchall()
        ctx.known[account] = {r[0] for r in rows}
        frontier.extend(q.seed_pack(account, window))

        if window:
            # Daily mode reuses everything previous runs learned: every
            # taught domain/address becomes an opening query instead of
            # waiting to be re-discovered through a positive.
            learned = ctx.conn.execute(
                "SELECT match_type, value FROM sender_rule"
                " WHERE verdict IN ('positive', 'undecided')"
            ).fetchall()
            entities = [
                Entity(kind="domain" if m == "domain" else "address", value=v)
                for m, v in learned
                if not (m == "domain" and _is_excluded_domain(v.lower()))
            ]
            for e in entities:
                ctx.seen_entities.add((e.kind, e.value))
            frontier.extend(q.entity_queries(entities, account, window))

    ctx.emit.emit(
        "run_started",
        accounts=state["accounts"],
        window_days=window,
        seed_queries=len(frontier),
        known_messages={a: len(ids) for a, ids in ctx.known.items()},
        started_at=state["run_started_at"],
    )
    return {
        "frontier": frontier,
        "pending_ids": {a: [] for a in state["accounts"]},
        "hop": 0,
        "counters": {},
        "facts": [],
    }


def list_frontier(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Run every queued query; collect ids nobody has ingested yet.

    Listing is cheap (500 ids per call) and fetching is not — this is where
    the 3.4x fetch saving is actually realized, because the skip set removes
    already-known ids *before* any ``messages.get`` is paid for.
    """
    ctx = _ctx(config)
    counters = dict(state["counters"])
    pending = {a: list(v) for a, v in state["pending_ids"].items()}
    queued = {a: set(v) for a, v in pending.items()}

    for query in state["frontier"]:
        known = ctx.known.setdefault(query.account, set())
        if query.q.startswith("thread:"):
            ids = ctx.source.thread_message_ids(query.account, query.q[7:])
        else:
            ids = list(ctx.source.search_ids(query.account, query.q))
        fresh = [i for i in ids if i not in known and i not in queued[query.account]]
        pending[query.account].extend(fresh)
        queued[query.account].update(fresh)
        counters["queries_run"] = counters.get("queries_run", 0) + 1
        counters["listed"] = counters.get("listed", 0) + len(ids)
        counters["listed_known"] = counters.get("listed_known", 0) + (
            len(ids) - len(fresh)
        )
        ctx.emit.emit(
            "query_yield",
            origin=query.origin,
            q=query.q,
            matched=len(ids),
            new=len(fresh),
            hop=state["hop"],
        )

    counters["to_fetch"] = sum(len(v) for v in pending.values())
    ctx.emit.emit("counters", **counters)
    return {"frontier": [], "pending_ids": pending, "counters": counters}


def fetch(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Pull every pending id through the idempotent ingest write path.

    Same ordering contract as v1 ingest: raw bytes land durably first, the
    row second, committed per batch — a killed run re-lists cheaply and skips
    everything that already made it.
    """
    ctx = _ctx(config)
    counters = dict(state["counters"])
    result = IngestResult(account="*")
    total = sum(len(v) for v in state["pending_ids"].values())
    done = 0
    for account, ids in state["pending_ids"].items():
        known = ctx.known.setdefault(account, set())
        for i in range(0, len(ids), FETCH_BATCH):
            for message_id in ids[i : i + FETCH_BATCH]:
                try:
                    raw = ctx.source.get_one(account, message_id)
                    ingest_one(
                        raw,
                        storage=ctx.storage,
                        messages=ctx.repos.messages,
                        channel="email",
                        out=result,
                    )
                    known.add(message_id)
                except Exception as exc:  # one bad message never costs the run
                    result.errors.append(f"{message_id}: {type(exc).__name__}: {exc}")
            ctx.conn.commit()
            done = min(done + FETCH_BATCH, total)
            ctx.emit.emit(
                "fetch_progress",
                fetched=done,
                total=total,
                stored=result.stored,
                errors=len(result.errors),
                hop=state["hop"],
            )
    fetched_now = result.stored + result.already_stored
    counters["fetched"] = counters.get("fetched", 0) + fetched_now
    counters["ingested"] = counters.get("ingested", 0) + result.rows_inserted
    counters["fetch_errors"] = counters.get("fetch_errors", 0) + len(result.errors)
    ctx.emit.emit("counters", **counters)
    return {
        "pending_ids": {a: [] for a in state["pending_ids"]},
        "counters": counters,
    }


def classify(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Classify everything unclassified — unchanged v1 machinery, batched.

    Prefilter → thread/rule carry → LLM. The scrape changes *what gets
    fetched*, never how a message is judged.
    """
    ctx = _ctx(config)
    counters = dict(state["counters"])
    facts: list[str] = []
    watermark = datetime.now(UTC)
    meter = _live_meter(ctx, state)
    status = "done"
    try:
        while True:
            result = classify_pending(
                storage=ctx.storage,
                llm=ctx.llm,
                repos=ctx.repos,
                conn=ctx.conn,
                limit=CLASSIFY_BATCH,
                escalation_llm=ctx.judge,
                triage_llm=getattr(ctx, "triage_llm", None),
                llm_workers=_llm_workers(),
                meter=meter,
            )
            if result.seen == 0:
                break
            if result.errors and result.seen == len(result.errors):
                # Every message this pass errored — an erroring message stays
                # unclassified, so another pass would re-select exactly the
                # same rows forever. Live-caught: the mailbox's one poison
                # message (raw bytes unreadable) span a run for 3,500+
                # iterations before this break existed. Report and move on;
                # the rows stay visible to the next run and to
                # `jobd classify`.
                status = "failed"
                counters["classify_errors"] = (
                    counters.get("classify_errors", 0) + len(result.errors)
                )
                ctx.emit.emit(
                    "classify_stalled",
                    errors=result.errors[:3],
                    count=len(result.errors),
                )
                break
            for key in (
                "filtered_out",
                "triaged_out",
                "triage_calls",
                "carried_forward",
                "llm_calls",
                "not_job_related",
                "queued_for_review",
                "recorded",
                "companies_created",
                "applications_created",
                "stage_events",
                "rules_learned",
            ):
                counters[key] = counters.get(key, 0) + getattr(result, key)
            counters["prompt_tokens"] = float(
                getattr(ctx.llm, "prompt_tokens", 0)
            )
            counters["completion_tokens"] = float(
                getattr(ctx.llm, "completion_tokens", 0)
            )
            counters["cost_usd"] = round(
                float(getattr(ctx.llm, "cost_usd", 0.0)), 4
            )
            counters["classified"] = counters.get("classified", 0) + (
                result.seen - len(result.errors)
            )
            if result.errors:
                counters["classify_errors"] = (
                    counters.get("classify_errors", 0) + len(result.errors)
                )
            ctx.emit.emit(
                "classify_progress",
                hop=state["hop"],
                **{
                    k: counters.get(k, 0)
                    for k in (
                        "classified",
                        "recorded",
                        "filtered_out",
                        "llm_calls",
                        "queued_for_review",
                    )
                },
            )
            for fact in new_company_facts(ctx.conn, watermark) + stage_facts(
                ctx.conn, watermark
            ):
                facts.append(fact)
                ctx.emit.emit("fact", text=fact, hop=state["hop"])
            watermark = datetime.now(UTC)
    except BaseException:
        # GuardAbort (budget/balance) is a BaseException; either way the
        # pipeline_run row must not stay "running" forever.
        status = "failed"
        raise
    finally:
        meter.finish(status)
    ctx.emit.emit("counters", **counters)
    return {"counters": counters, "facts": facts}


def audit(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """LLM second-look at everything this run touched — see audit.py.

    Runs between classify and harvest on purpose: a junk link cleaned here
    never becomes an entity the expansion loop would have gone querying for.
    """
    ctx = _ctx(config)
    touched = ctx.conn.execute(
        "SELECT id FROM company"
        " WHERE created_at >= %(since)s OR last_seen_at >= %(since)s",
        {"since": state["run_started_at"]},
    ).fetchall()
    if not touched:
        return {}
    ctx.emit.emit("audit_started", companies=len(touched), hop=state["hop"])
    result = run_audit(
        ctx.conn, ctx.llm, judge=ctx.judge, emit=ctx.emit, apply=True,
        company_ids=[row[0] for row in touched],
    )
    counters = dict(state["counters"])
    counters["audited_messages"] = (
        counters.get("audited_messages", 0) + result.messages_checked
    )
    counters["audit_unlinked"] = (
        counters.get("audit_unlinked", 0) + result.messages_unlinked
    )
    counters["audit_merged"] = (
        counters.get("audit_merged", 0) + result.applications_merged
    )
    counters["audit_rules"] = (
        counters.get("audit_rules", 0) + result.rules_taught + result.rules_demoted
    )
    ctx.emit.emit(
        "audit_done",
        checked=result.messages_checked,
        unlinked=result.messages_unlinked,
        merged=result.applications_merged,
        rules=result.rules_taught + result.rules_demoted,
        hop=state["hop"],
    )
    ctx.emit.emit("counters", **counters)
    return {"counters": counters}


def harvest(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Turn this hop's confident positives into next hop's queries."""
    ctx = _ctx(config)
    since = state["run_started_at"]
    fresh: list[Entity] = []

    domains = ctx.conn.execute(
        "SELECT DISTINCT domain, canonical_name FROM company"
        " WHERE created_at >= %s AND domain IS NOT NULL",
        (since,),
    ).fetchall()
    for domain, name in domains:
        d = domain.lower()
        if not _is_excluded_domain(d) and ("domain", d) not in ctx.seen_entities:
            ctx.seen_entities.add(("domain", d))
            fresh.append(Entity(kind="domain", value=d, via=name))

    addresses = ctx.conn.execute(
        "SELECT DISTINCT ci.identifier FROM contact_identity ci"
        " JOIN contact c ON c.id = ci.contact_id"
        " WHERE ci.channel = 'email' AND c.created_at >= %s",
        (since,),
    ).fetchall()
    for (addr,) in addresses:
        a = addr.lower()
        if ("address", a) not in ctx.seen_entities:
            ctx.seen_entities.add(("address", a))
            fresh.append(Entity(kind="address", value=a))

    threads = ctx.conn.execute(
        "SELECT DISTINCT thread_id, account FROM message"
        " WHERE company_id IS NOT NULL AND thread_id IS NOT NULL"
        "   AND classified_at >= %s",
        (since,),
    ).fetchall()

    # Genuine-interaction gating: only expand threads with ≥1 outbound message.
    # One-way marketing threads (no user reply) waste Gmail API list calls —
    # they pull the whole thread but yield nothing new. A thread with user
    # engagement (sent reply/forward) is a conversation worth expanding.
    threads_with_outbound = set()
    if threads:
        thread_ids = [t[0] for t in threads]
        # Batch query: check each thread for outbound messages in one round-trip.
        outbound_rows = ctx.conn.execute(
            "SELECT DISTINCT thread_id FROM message"
            " WHERE thread_id = ANY(%s) AND direction = 'outbound'",
            (thread_ids,),
        ).fetchall()
        threads_with_outbound = {row[0] for row in outbound_rows}

    frontier: list[Query] = []
    for account in state["accounts"]:
        frontier.extend(q.entity_queries(fresh, account, state.get("window_days")))
    threads_gated = 0
    for thread_id, account in threads:
        if ("thread", thread_id) not in ctx.seen_entities:
            ctx.seen_entities.add(("thread", thread_id))
            # Gate: skip threads with no outbound messages (one-way marketing).
            if thread_id not in threads_with_outbound:
                threads_gated += 1
                continue
            frontier.append(
                Query(q=f"thread:{thread_id}", origin="expand:thread", account=account)
            )

    hop = state["hop"] + 1
    if threads_gated > 0:
        ctx.emit.emit(
            "threads_gated",
            gated=threads_gated,
            expanded=len(threads) - threads_gated,
            hop=hop,
        )
    if hop > MAX_HOPS and frontier:
        ctx.emit.emit("expansion_capped", dropped=len(frontier), hop=hop)
        frontier = []
    ctx.emit.emit(
        "expansion",
        hop=hop,
        new_domains=sum(1 for e in fresh if e.kind == "domain"),
        new_addresses=sum(1 for e in fresh if e.kind == "address"),
        new_threads=len(threads),
        queries=len(frontier),
    )
    for e in fresh[:150]:
        # `entity_kind`, not `kind` — the SSE frame's own `kind` field is the
        # event name, and a payload key would silently overwrite it.
        ctx.emit.emit(
            "entity", entity_kind=e.kind, value=e.value, via=e.via, hop=hop
        )
        if e.kind == "domain" and e.via:
            ctx.emit.emit(
                "fact", text=f"Learned to watch {e.value} (via {e.via})", hop=hop
            )
    return {"frontier": frontier, "hop": hop}


def residual(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Queue the catch-all pass over direct-addressed mail.

    Runs once, after expansion converges, so the skip set has already
    claimed everything cheaper passes found. Measured: this pool held 100%
    of the positives everything else missed.
    """
    ctx = _ctx(config)
    frontier = [
        query
        for account in state["accounts"]
        for query in q.residual_pack(account, state.get("window_days"))
    ]
    ctx.emit.emit("residual_started", queries=len(frontier))
    return {"frontier": frontier, "residual_done": True}


def report(state: ScrapeState, config: RunnableConfig) -> dict[str, Any]:
    """Persist the run row; close the stream."""
    ctx = _ctx(config)
    counters = dict(state["counters"])
    started = datetime.fromisoformat(state["run_started_at"])
    counters["duration_s"] = round(
        (datetime.now(UTC) - started).total_seconds(), 1
    )
    counters["api_calls"] = float(getattr(ctx.source, "api_calls", 0))
    counters["prompt_tokens"] = float(getattr(ctx.llm, "prompt_tokens", 0))
    counters["completion_tokens"] = float(getattr(ctx.llm, "completion_tokens", 0))
    counters["cost_usd"] = round(float(getattr(ctx.llm, "cost_usd", 0.0)), 4)
    ctx.conn.execute(
        """
        INSERT INTO scrape_run
            (started_at, finished_at, accounts, window_days, counters)
        VALUES (%(started)s, now(), %(accounts)s, %(window)s, %(counters)s)
        """,
        {
            "started": state["run_started_at"],
            "accounts": state["accounts"],
            "window": state.get("window_days"),
            "counters": json.dumps(counters),
        },
    )
    ctx.conn.commit()
    closing = mailbox_facts(ctx.conn, counters)
    for fact in closing:
        ctx.emit.emit("fact", text=fact, hop=state["hop"])
    ctx.emit.emit("done", counters=counters, facts=state.get("facts", []) + closing)
    return {"done": True}


def route(state: ScrapeState) -> Literal["list", "residual", "report"]:
    if state["frontier"]:
        return "list"
    if not state.get("residual_done"):
        return "residual"
    return "report"


def build_graph() -> Any:
    """Compile the state machine. Pure wiring — every behaviour above."""
    graph = StateGraph(ScrapeState)
    graph.add_node("plan", plan)
    graph.add_node("list", list_frontier)
    graph.add_node("fetch", fetch)
    graph.add_node("classify", classify)
    graph.add_node("audit", audit)
    graph.add_node("harvest", harvest)
    graph.add_node("residual", residual)
    graph.add_node("report", report)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "list")
    graph.add_edge("list", "fetch")
    graph.add_edge("fetch", "classify")
    graph.add_edge("classify", "audit")
    graph.add_edge("audit", "harvest")
    graph.add_conditional_edges(
        "harvest", route, {"list": "list", "residual": "residual", "report": "report"}
    )
    graph.add_edge("residual", "list")
    graph.add_edge("report", END)
    return graph.compile()
