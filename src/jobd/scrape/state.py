"""Graph state for the seed-and-expand scrape.

Two-tier on purpose:

* ``ScrapeState`` is the small, serializable dict LangGraph threads through
  the nodes — counters, the query frontier, discovered entities, facts. It is
  what a checkpoint would capture and what the event stream summarizes.
* ``RunContext`` is the heavy, non-serializable machinery — API clients,
  repositories, the seen-id set. It rides in ``config["configurable"]``, never
  in state. Losing it on a crash costs nothing: every node writes through to
  Postgres/storage idempotently (v1's I2/I3 invariants), so a re-run skips
  what already landed. Idempotency, not checkpoint restore, is the resume
  mechanism — the same trade v1's day-batched backfill makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from operator import add
from typing import Annotated, Any, TypedDict


@dataclass(slots=True)
class Query:
    """One Gmail search to run: what to ask and why we're asking."""

    q: str
    #: Which stage produced it — "seed:sent", "seed:ats", "expand:domain", ...
    #: Carried into events so the dashboard can attribute yield per family.
    origin: str
    account: str


@dataclass(slots=True)
class Entity:
    """Something a confident positive taught us to search for."""

    kind: str  # "domain" | "address" | "thread"
    value: str
    #: Human-readable provenance for the dashboard ticker, e.g. the company
    #: whose message taught it.
    via: str = ""


class ScrapeState(TypedDict, total=False):
    """What flows through the graph. Small by design — see module docstring."""

    accounts: list[str]
    #: None = full backfill; N = only mail newer than N days (the daily mode).
    window_days: int | None
    #: Queries not yet listed. Nodes drain and refill this.
    frontier: list[Query]
    #: Message ids listed but not yet fetched+ingested, per account.
    pending_ids: dict[str, list[str]]
    #: Entities discovered this hop, not yet turned into queries.
    new_entities: list[Entity]
    #: Expansion hop counter (0 = seeds).
    hop: int
    #: Monotonic counters the dashboard renders: listed, fetched, ingested,
    #: classified, recorded, rules_learned, api_calls, prompt_tokens,
    #: cost_usd... Values are numbers, not strictly ints — cost is a float.
    counters: dict[str, float]
    #: Append-only human-readable discoveries ("New company: Acme").
    facts: Annotated[list[str], add]
    #: ISO timestamp the run began — the watermark harvest/facts queries
    #: compare row `created_at`s against.
    run_started_at: str
    #: The catch-all direct-mail pass has been queued (it runs exactly once).
    residual_done: bool
    #: Set by report when the run is finished.
    done: bool


@dataclass
class RunContext:
    """Heavy dependencies, assembled once per run in service.py."""

    source: Any  # GmailSource
    storage: Any  # Storage port
    repos: Any  # repository bundle
    conn: Any  # psycopg connection
    llm: Any  # extractor LLMProvider
    #: Application-audit judge — a stronger model than `llm`, or None to
    #: reuse `llm`. See run_audit's docstring for why the tiers split.
    judge: Any
    emit: Any  # events.Emitter
    #: Batch triage model — cheap flash for label-only 240-char snippet
    #: prefiltering before full extraction. None to skip triage entirely.
    triage_llm: Any = None
    #: External ids already in the DB for each account — the skip set that
    #: makes daily re-runs cheap. Loaded once, grown in-memory as we ingest.
    known: dict[str, set[str]] = field(default_factory=dict)
    #: Entities already queried, so expansion converges instead of cycling.
    seen_entities: set[tuple[str, str]] = field(default_factory=set)
