"""Composition root for a scrape run.

The one place that knows concrete adapters. The graph (graph.py) sees only a
``RunContext`` of duck-typed dependencies; the CLI and the dashboard both call
:func:`run_scrape` and differ only in the event sink they attach and the
thread they call from.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from jobd.config import load_settings
from jobd.scrape.events import Emitter, Event
from jobd.scrape.graph import build_graph
from jobd.scrape.state import RunContext, ScrapeState


class ScrapeConfigError(RuntimeError):
    """The environment is missing something a run cannot start without."""


def _storage(bucket: str | None, local_store: Path | None) -> Any:
    if local_store is None and os.environ.get("JOBD_LOCAL_STORE"):
        # Filesystem archive as the raw store — the no-cloud deployment, and
        # the fallback when S3 credentials are absent/expired. Write-once,
        # content-addressed, so `aws s3 sync` reconciles it into a bucket
        # later without conflicts.
        local_store = Path(os.environ["JOBD_LOCAL_STORE"])
    if local_store:
        from jobd.adapters.filesystem import FilesystemStorage

        return FilesystemStorage(local_store)
    if not bucket:
        raise ScrapeConfigError(
            "No raw storage configured — set JOBD_BUCKET or pass a local store."
        )
    from jobd.adapters.caching import CachingStorage
    from jobd.adapters.s3 import S3Storage

    return CachingStorage(S3Storage(bucket), Path.home() / ".jobd" / "cache" / "raw")


def probe_raw_storage(storage: Any) -> None:
    """One cheap existence probe against the backing store, before any work.

    The failure this prevents, live-caught: an expired AWS session let a
    whole window run — 542 queries, 71 messages to fetch — and every single
    fetch died one at a time (raw-bytes-first ordering means no S3, no
    ingest), surfaced as 135 per-message errors instead of one sentence.
    Same shape as the OpenRouter credit guard: fail before burning anything,
    and name the fix in the message.
    """
    try:
        storage.exists("healthcheck/preflight")
    except Exception as exc:
        raise ScrapeConfigError(
            "Raw storage unreachable — the AWS session has likely expired. "
            "Run `aws login`, then start the scrape again. "
            f"({type(exc).__name__})"
        ) from exc


def check_raw_storage(
    bucket: str | None = None, local_store: Path | None = None
) -> None:
    """Preflight for callers that haven't built a storage yet (the dashboard's
    start endpoint) — build one exactly the way the run will, probe it."""
    probe_raw_storage(_storage(bucket or os.environ.get("JOBD_BUCKET"), local_store))


def run_scrape(
    *,
    accounts: list[str],
    window_days: int | None = None,
    model: str = "default",
    bucket: str | None = None,
    local_store: Path | None = None,
    sink: Any = None,
    emitter: Emitter | None = None,
) -> dict[str, Any]:
    """Run the full seed-and-expand graph for one or more mailboxes.

    Args:
        accounts: Gmail accounts to scrape — each needs a token from
            ``jobd auth gmail`` first.
        window_days: ``None`` is the full backfill. A number is the daily
            mode: every query gets an ``after:`` bound and previously learned
            entities seed the frontier directly. Overlap between runs is free
            — ingest is idempotent, listing skips known ids before fetching.
        model: Extractor for messages the free tiers (prefilter, thread and
            rule carry) can't settle. ``default`` resolves ``JOBD_MODEL``
            then the built-in default; ``ollama/<model>`` runs fully local;
            anything else goes through LiteLLM (needs its API key in the
            environment).
        sink: Optional callable receiving every :class:`Event` — the CLI's
            printer. The dashboard passes ``emitter`` instead and reads it
            from its SSE route.

    Returns the final counters dict (also carried on the terminal ``done``
    event).
    """
    settings = load_settings()
    if not settings.database_url:
        raise ScrapeConfigError("DATABASE_URL is unset.")

    from jobd.adapters.gmail import GmailSource
    from jobd.adapters.llm import load_provider
    from jobd.adapters.postgres import repositories
    from jobd.adapters.secrets import TokenStore

    emit = emitter or Emitter()
    if sink is not None:
        emit.sink = sink

    llm = load_provider(model)
    judge_model = os.environ.get("JOBD_JUDGE_MODEL")
    judge = (
        load_provider(judge_model, max_tokens=8192) if judge_model else None
    )

    storage = _storage(bucket or os.environ.get("JOBD_BUCKET"), local_store)
    probe_raw_storage(storage)

    with psycopg.connect(settings.database_url) as conn:
        ctx = RunContext(
            source=GmailSource(TokenStore()),
            storage=storage,
            repos=repositories(conn),
            conn=conn,
            llm=llm,
            judge=judge,
            emit=emit,
        )
        state: ScrapeState = {
            "accounts": accounts,
            "window_days": window_days,
            "run_started_at": datetime.now(UTC).isoformat(),
            "facts": [],
        }
        graph = build_graph()
        final = graph.invoke(
            state,
            config={"configurable": {"ctx": ctx}, "recursion_limit": 100},
        )
        counters: dict[str, Any] = final.get("counters", {})
        counters["api_calls"] = ctx.source.api_calls
        return counters


__all__ = ["Emitter", "Event", "ScrapeConfigError", "run_scrape"]
