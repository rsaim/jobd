"""Live run metrics — the `pipeline_run` row a long job keeps current.

Every long-running pipeline (classify, review sweep, audit, envelope
backfill) owns exactly one `pipeline_run` row and updates it in place as it
works. That gives any observer — the Runs page, `jobd runs`, a shell loop —
one cheap query for progress, rate, ETA, spend, and stall detection, without
tailing process output. Designed for the millions-of-emails case:

* The writer owns its row exclusively, so a flush is one UPDATE of absolute
  values. No read-modify-write, no counter-merge SQL, no row contention —
  eight partition workers are eight rows sharing a ``group_id``.
* Flushes are throttled (every ~2s or every 500 events) on a dedicated
  autocommit connection. Metrics never join the work transaction: a rollback
  of real work does not lose progress numbers, and a metrics hiccup never
  rolls back real work.
* Everything degrades to a no-op. No DATABASE_URL, a failed connect, a
  dropped connection mid-run — the pipeline keeps working and only the
  progress row goes quiet. Metrics must never be the reason a run died.

Token/cost numbers are read straight off the LLM providers' own cumulative
counters (``prompt_tokens`` / ``completion_tokens`` / ``cost_usd`` on
`LiteLLMProvider`) at flush time — no per-call plumbing through the pipeline.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any
from uuid import UUID, uuid4


class NullMeter:
    """The do-nothing twin. Every pipeline accepts a meter; passing this one
    (or getting it back from :meth:`RunMeter.start` when metrics can't run)
    means zero conditional code at the call sites."""

    id: UUID | None = None
    group_id: UUID | None = None

    def bump(self, outcome: str | None = None, n: int = 1) -> None: ...

    def error(self, message: str) -> None: ...

    def set_total(self, total: int | None) -> None: ...

    def set_counter(self, key: str, value: Any) -> None: ...

    def flush(self, force: bool = False) -> None: ...

    def finish(self, status: str = "done") -> None: ...

    def __enter__(self) -> "NullMeter":
        return self

    def __exit__(self, *exc: Any) -> None: ...


class RunMeter(NullMeter):
    """One `pipeline_run` row, kept live. Thread-safe: extract pools bump
    from worker threads while the main loop bumps from its own."""

    FLUSH_SECONDS = 2.0
    FLUSH_EVENTS = 500

    def __init__(
        self,
        conn: Any,
        *,
        kind: str,
        total: int | None = None,
        args: dict[str, Any] | None = None,
        worker: str = "",
        group_id: UUID | None = None,
        providers: list[Any] | None = None,
    ) -> None:
        self._conn = conn
        self.id = uuid4()
        self.group_id = group_id or self.id
        self._kind = kind
        self._total = total
        self._processed = 0
        self._counters: dict[str, Any] = {}
        self._errors = 0
        self._last_error: str | None = None
        self._providers = providers or []
        self._lock = threading.Lock()
        self._dirty_events = 0
        self._last_flush = time.monotonic()
        self._dead = False
        from psycopg.types.json import Jsonb

        self._jsonb = Jsonb
        conn.execute(
            "INSERT INTO pipeline_run (id, group_id, kind, worker, args, total)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                self.id,
                self.group_id,
                kind,
                worker,
                Jsonb(args or {}),
                total,
            ),
        )
        # The heartbeat exists for the quiet stretches: a pool can spend
        # minutes on its first big unit of work with zero bumps, and a row
        # that only moves on events would read as stalled the whole time.
        # A daemon thread keeps updated_at honest either way; readers only
        # call "stalled" on 20s of true silence.
        threading.Thread(target=self._heartbeat, daemon=True).start()

    # -- construction ------------------------------------------------------

    @classmethod
    def start(
        cls,
        *,
        kind: str,
        total: int | None = None,
        args: dict[str, Any] | None = None,
        worker: str = "",
        group_id: UUID | str | None = None,
        providers: list[Any] | None = None,
        dsn: str | None = None,
    ) -> NullMeter:
        """Open the meter, or a `NullMeter` when metrics can't run here."""
        import psycopg

        url = dsn or os.environ.get("DATABASE_URL")
        if not url:
            return NullMeter()
        if isinstance(group_id, str):
            try:
                group_id = UUID(group_id)
            except ValueError:
                group_id = None
        try:
            conn = psycopg.connect(url, autocommit=True)
            return cls(
                conn,
                kind=kind,
                total=total,
                args=args,
                worker=worker,
                group_id=group_id,
                providers=providers,
            )
        except Exception:
            return NullMeter()

    # -- the write API the pipelines call ----------------------------------

    def bump(self, outcome: str | None = None, n: int = 1) -> None:
        with self._lock:
            self._processed += n
            if outcome:
                self._counters[outcome] = int(self._counters.get(outcome, 0)) + n
            self._dirty_events += n
        self._maybe_flush()

    def error(self, message: str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = message[:500]
            self._dirty_events += 1
        self._maybe_flush()

    def set_total(self, total: int | None) -> None:
        with self._lock:
            self._total = total
        self.flush(force=True)

    def set_counter(self, key: str, value: Any) -> None:
        """Non-additive annotations — current phase name, current batch id."""
        with self._lock:
            self._counters[key] = value
            self._dirty_events += 1
        self._maybe_flush()

    def flush(self, force: bool = False) -> None:
        if self._dead:
            return
        with self._lock:
            if not force and self._dirty_events == 0:
                return
            snapshot = (
                self._total,
                self._processed,
                dict(self._counters),
                self._errors,
                self._last_error,
            )
            self._dirty_events = 0
            self._last_flush = time.monotonic()
        tokens_in = sum(int(getattr(p, "prompt_tokens", 0) or 0) for p in self._providers)
        tokens_out = sum(
            int(getattr(p, "completion_tokens", 0) or 0) for p in self._providers
        )
        cost = sum(float(getattr(p, "cost_usd", 0.0) or 0.0) for p in self._providers)
        calls = sum(int(getattr(p, "calls", 0) or 0) for p in self._providers)
        total, processed, counters, errors, last_error = snapshot
        try:
            self._conn.execute(
                "UPDATE pipeline_run SET total=%s, processed=%s, counters=%s,"
                " llm_calls=%s, llm_tokens_in=%s, llm_tokens_out=%s,"
                " llm_cost_usd=%s, errors=%s, last_error=%s, updated_at=now()"
                " WHERE id=%s",
                (
                    total,
                    processed,
                    self._jsonb(counters),
                    calls,
                    tokens_in,
                    tokens_out,
                    round(cost, 6),
                    errors,
                    last_error,
                    self.id,
                ),
            )
        except Exception:
            # A broken metrics connection must never take the run down with
            # it. Go quiet; the reader's stall detection surfaces the silence.
            self._dead = True

    def finish(self, status: str = "done") -> None:
        self.flush(force=True)
        if not self._dead:
            try:
                self._conn.execute(
                    "UPDATE pipeline_run SET status=%s, finished_at=now(),"
                    " updated_at=now() WHERE id=%s",
                    (status, self.id),
                )
            except Exception:
                pass
        try:
            self._conn.close()
        except Exception:
            pass
        self._dead = True

    # -- internals ---------------------------------------------------------

    def _heartbeat(self) -> None:
        while not self._dead:
            time.sleep(self.FLUSH_SECONDS * 2)
            self.flush(force=True)

    def _maybe_flush(self) -> None:
        if self._dead:
            return
        if (
            self._dirty_events >= self.FLUSH_EVENTS
            or time.monotonic() - self._last_flush >= self.FLUSH_SECONDS
        ):
            self.flush()

    def __enter__(self) -> "RunMeter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finish("failed" if exc_type else "done")


def read_runs(conn: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    """Rows for the Runs page / `jobd runs`, active first, with derived
    rate / ETA / stall so every reader shows the same arithmetic."""
    rows = conn.execute(
        """
        SELECT id, group_id, kind, worker, args, status, total, processed,
               counters, llm_calls, llm_tokens_in, llm_tokens_out,
               llm_cost_usd, errors, last_error, started_at, updated_at,
               finished_at,
               extract(epoch FROM (coalesce(finished_at, now()) - started_at))
                   AS elapsed_s,
               extract(epoch FROM (now() - updated_at)) AS silent_s
        FROM pipeline_run
        ORDER BY (finished_at IS NULL) DESC, started_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        (
            rid, gid, kind, worker, args, status, total, processed, counters,
            llm_calls, tok_in, tok_out, cost, errors, last_error,
            started_at, updated_at, finished_at, elapsed_s, silent_s,
        ) = r
        elapsed = max(float(elapsed_s or 0.0), 0.001)
        rate = processed / elapsed  # items per second over the whole run
        remaining = (total - processed) if (total and total > processed) else None
        eta_s = int(remaining / rate) if (remaining and rate > 0) else None
        active = finished_at is None
        out.append(
            {
                "id": str(rid),
                "group_id": str(gid),
                "kind": kind,
                "worker": worker,
                "args": args if isinstance(args, dict) else json.loads(args or "{}"),
                "status": status if not active else "running",
                "total": total,
                "processed": processed,
                "counters": counters
                if isinstance(counters, dict)
                else json.loads(counters or "{}"),
                "llm_calls": llm_calls,
                "llm_tokens_in": tok_in,
                "llm_tokens_out": tok_out,
                "llm_cost_usd": float(cost),
                "errors": errors,
                "last_error": last_error,
                "started_at": started_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "finished_at": finished_at.isoformat() if finished_at else None,
                "elapsed_s": int(elapsed),
                "rate_per_min": round(rate * 60.0, 1),
                "eta_s": eta_s,
                # 20s of silence from a live writer that flushes every 2s is
                # ten missed beats — a hung call or a dead worker, not jitter.
                "stalled": bool(active and float(silent_s or 0.0) > 20.0),
            }
        )
    return out
