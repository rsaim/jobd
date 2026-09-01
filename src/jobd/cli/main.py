"""`jobd` entrypoint.

P4(b) requires a CLI for every operation, so the command surface is declared
here in M2 and filled in by the milestone that owns each verb. Unimplemented
commands **exit non-zero with the owning milestone named**, rather than printing
a friendly nothing — a stub that exits zero is indistinguishable from a working
command in a script, and that is how a scaffold quietly becomes a bug report.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import click

from jobd import __version__
from jobd.adapters.postgres import Migrator
from jobd.config import load_settings

_MILESTONES = {
    "ingest linkedin": "M6",
}


def _not_yet(verb: str) -> None:
    """Fail loudly, naming the milestone that lands this verb."""
    raise click.ClickException(
        f"`jobd {verb}` lands in {_MILESTONES[verb]}. Not built yet."
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="jobd")
def main() -> None:
    """jobd — a daemon for your job search.

    Local-first. Raw mail never leaves your machine or your own S3 bucket, and
    nothing is ever sent without your recorded approval.
    """


@main.group()
def config() -> None:
    """Inspect the resolved configuration."""


@config.command("show")
def config_show() -> None:
    """Print the active profile and where state lives."""
    settings = load_settings()
    click.echo(f"profile:      {settings.profile.value}")
    click.echo(f"database_url: {settings.database_url or '(unset)'}")


@main.group()
def migrate() -> None:
    """Apply or reverse database migrations."""


def _migrator() -> Migrator:
    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset. Nothing to migrate.")
    return Migrator(settings.database_url)


@migrate.command("up")
@click.option("--to", type=int, help="Stop after this version.")
def migrate_up(to: int | None) -> None:
    """Apply pending migrations."""
    applied = _migrator().up(to=to)
    click.echo("\n".join(applied) if applied else "Already up to date.")


@migrate.command("down")
@click.option("--to", type=int, default=0, show_default=True, help="Revert down to.")
@click.confirmation_option(prompt="Reverting drops tables. Continue?")
def migrate_down(to: int) -> None:
    """Reverse applied migrations. Derived data only — raw storage is untouched."""
    reverted = _migrator().down(to=to)
    click.echo("\n".join(reverted) if reverted else "Nothing to revert.")


@migrate.command("status")
def migrate_status() -> None:
    """Show which migrations have run."""
    migrator = _migrator()
    applied = migrator.applied()
    for migration in migrator.all:
        mark = "applied" if migration.version in applied else "pending"
        click.echo(f"{mark:>7}  {migration}")


@main.group()
def ingest() -> None:
    """Pull messages from a source into raw storage."""


@ingest.command("gmail")
@click.option("--since", help="Lower bound on arrival, ISO-8601. Full sync only.")
@click.option("--account", multiple=True, required=True, help="Account to ingest.")
@click.option(
    "--full",
    is_flag=True,
    help="Ignore the stored cursor and re-walk everything. Safe: idempotent.",
)
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket. Omit to use local.")
@click.option(
    "--local-store",
    type=click.Path(path_type=Path),
    help="Write raw to this directory instead of S3.",
)
def ingest_gmail(
    since: str | None,
    account: tuple[str, ...],
    full: bool,
    bucket: str | None,
    local_store: Path | None,
) -> None:
    """Ingest Gmail, incrementally and idempotently.

    Running this twice is a no-op by construction: keys are content hashes,
    raw storage is write-once, and message.storage_key is UNIQUE (I2).
    """
    from jobd.adapters.gmail import GmailSource, HistoryTooOld
    from jobd.adapters.postgres.repositories import MessageRepository
    from jobd.adapters.secrets import TokenStore
    from jobd.services import ingest_account

    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset.")

    storage = _storage(bucket, local_store)
    started = _parse_since(since)
    source = GmailSource(TokenStore())

    import psycopg

    with psycopg.connect(settings.database_url) as conn:
        for address in account:
            run_id = _start_run(settings.database_url, source.name, address)
            try:
                result = ingest_account(
                    source=source,
                    storage=storage,
                    messages=MessageRepository(conn),
                    conn=conn,
                    account=address,
                    since=started,
                    resume=not full,
                )
            except HistoryTooOld as exc:
                _finish_run(settings.database_url, run_id, failure=str(exc))
                raise click.ClickException(f"{exc}") from exc
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
                _finish_run(settings.database_url, run_id, failure=failure)
                raise
            _finish_run(settings.database_url, run_id, result=result)
            _report(result, source.api_calls)


def _start_run(
    database_url: str, source: str, account: str, *, day: Any = None
) -> Any:
    """Log that a run began, before any fetching, and commit immediately.

    A dedicated autocommit connection, not the connection ``ingest_account``
    writes rows through: that connection's transaction lives until the whole
    run succeeds, and a run that gets SIGKILLed takes its own start record
    down with it — the exact failure mode this table exists to make visible.
    A row with `finished_at` still NULL after this commits means the process
    died mid-run, not that it never started.
    """
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO ingest_run (source, account, day) VALUES (%s, %s, %s)"
            " RETURNING id",
            (source, account, day),
        ).fetchone()
        assert row is not None  # RETURNING on a successful INSERT always yields one
        return row[0]


def _finish_run(
    database_url: str, run_id: Any, *, result: Any = None, failure: str | None = None
) -> None:
    """Close out a run row on its own autocommit connection. Exactly one of
    `result`/`failure` is given."""
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        if failure is not None:
            conn.execute(
                "UPDATE ingest_run SET finished_at = now(), failure = %s WHERE id = %s",
                (failure, run_id),
            )
            return
        conn.execute(
            "UPDATE ingest_run SET finished_at = now(), fetched = %s, stored = %s,"
            "  already_stored = %s, rows_inserted = %s, rows_existing = %s,"
            "  row_errors = %s WHERE id = %s",
            (
                result.fetched,
                result.stored,
                result.already_stored,
                result.rows_inserted,
                result.rows_existing,
                "\n".join(result.errors) if result.errors else None,
                run_id,
            ),
        )


def _day_already_done(database_url: str, source: str, account: str, day: Any) -> bool:
    """True if a prior run for this exact day succeeded. Checked before doing
    any work at all — the zero-API-call path for a backfill re-run."""
    import psycopg

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT 1 FROM ingest_run WHERE source = %s AND account = %s"
            "  AND day = %s AND finished_at IS NOT NULL AND failure IS NULL"
            " LIMIT 1",
            (source, account, day),
        ).fetchone()
        return row is not None


@ingest.command("gmail-day")
@click.option("--account", required=True, help="Account to ingest.")
@click.option("--day", required=True, help="UTC calendar day, YYYY-MM-DD.")
@click.option(
    "--force", is_flag=True, help="Re-check this day even if a prior run succeeded."
)
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket. Omit to use local.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Write raw here instead."
)
def ingest_gmail_day(
    account: str, day: str, force: bool, bucket: str | None, local_store: Path | None
) -> None:
    """Ingest one UTC calendar day for one account.

    The unit `gmail-backfill` runs one process per, and the one you would run
    by hand to retry a single failed day. Skips entirely, with no API calls,
    if this exact day already has a successful run recorded — pass --force to
    re-check anyway (still cheap: only ids not already in Postgres are
    fetched, see ingest_day).
    """
    from jobd.adapters.gmail import GmailSource
    from jobd.adapters.postgres.repositories import MessageRepository
    from jobd.adapters.secrets import TokenStore
    from jobd.services import ingest_day

    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset.")

    try:
        parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError as exc:
        raise click.ClickException(f"--day {day!r} is not YYYY-MM-DD") from exc

    storage = _storage(bucket, local_store)
    source = GmailSource(TokenStore())

    if not force and _day_already_done(
        settings.database_url, source.name, account, parsed_day
    ):
        click.echo(f"{account} {day}: already ingested, skipping.")
        return

    import psycopg

    run_id = _start_run(settings.database_url, source.name, account, day=parsed_day)
    try:
        with psycopg.connect(settings.database_url) as conn:
            result = ingest_day(
                source=source,
                storage=storage,
                messages=MessageRepository(conn),
                account=account,
                day=parsed_day,
            )
            conn.commit()
    except Exception as exc:
        _finish_run(
            settings.database_url, run_id, failure=f"{type(exc).__name__}: {exc}"
        )
        raise
    _finish_run(settings.database_url, run_id, result=result)
    click.echo(
        f"{account} {day}: fetched={result.fetched} stored={result.stored} "
        f"skipped_known={result.skipped_known} rows={result.rows_inserted} "
        f"api_calls={source.api_calls}"
    )
    for error in result.errors:
        click.echo(f"  ERROR {error}", err=True)


@ingest.command("gmail-backfill")
@click.option("--account", required=True, help="Account to ingest.")
@click.option("--since", required=True, help="First day, ISO-8601 (YYYY-MM-DD).")
@click.option(
    "--until",
    help="Last day, ISO-8601, inclusive. Defaults to today (UTC).",
)
@click.option(
    "--parallel",
    default=4,
    show_default=True,
    help="Concurrent day-worker processes. Gmail's per-user quota is shared "
    "across all of them — raising this trades speed for 429s.",
)
@click.option("--force", is_flag=True, help="Re-check every day, even completed ones.")
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket. Omit to use local.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Write raw here instead."
)
def ingest_gmail_backfill(
    account: str,
    since: str,
    until: str | None,
    parallel: int,
    force: bool,
    bucket: str | None,
    local_store: Path | None,
) -> None:
    """Backfill a date range as one OS process per day.

    A single process walking years of mail is fragile: one stall or OOM loses
    everything fetched after it, and there is no smaller unit than "start
    over" to retry. Splitting by day gives each day its own process, its own
    `ingest_run` row, and its own pass/fail — a stuck or killed day-worker
    costs that one day, not the backfill, and re-running this command finds
    every already-succeeded day and skips it for free.
    """
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    try:
        start_day = datetime.strptime(since, "%Y-%m-%d").date()
    except ValueError as exc:
        raise click.ClickException(f"--since {since!r} is not YYYY-MM-DD") from exc
    end_day = (
        datetime.strptime(until, "%Y-%m-%d").date()
        if until
        else datetime.now(UTC).date()
    )
    if end_day < start_day:
        raise click.ClickException("--until is before --since.")

    days = []
    day = start_day
    while day <= end_day:
        days.append(day)
        day += timedelta(days=1)

    def run_one(day: Any) -> tuple[Any, int]:
        cmd = [
            sys.executable,
            "-m",
            "jobd.cli",
            "ingest",
            "gmail-day",
            "--account",
            account,
            "--day",
            day.isoformat(),
        ]
        if force:
            cmd.append("--force")
        if bucket:
            cmd += ["--bucket", bucket]
        if local_store:
            cmd += ["--local-store", str(local_store)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
        return day, proc.returncode

    click.echo(f"{len(days)} day(s), {parallel} worker(s) in parallel.")
    failed: list[Any] = []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for day, code in pool.map(run_one, days):
            if code != 0:
                failed.append(day)

    if failed:
        raise click.ClickException(
            f"{len(failed)} day(s) failed: "
            f"{', '.join(d.isoformat() for d in failed)}. Re-run this command — "
            "completed days are skipped automatically, only failures retry."
        )
    click.echo("All days ingested.")


@ingest.command("backfill-senders")
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket of the raw store.")
@click.option(
    "--local-store", envvar="JOBD_LOCAL_STORE", type=Path, help="Filesystem raw store."
)
@click.option("--limit", default=100_000, show_default=True)
@click.option("--workers", default=32, show_default=True)
def ingest_backfill_senders(
    bucket: str | None, local_store: Path | None, limit: int, workers: int
) -> None:
    """Fill NULL sender/recipient columns from the raw store.

    Pure re-derive of envelope facts the v2 scrape writer never parsed;
    queue-pending messages go first. Safe to interrupt and re-run.
    """
    from jobd.services.ingest import backfill_envelopes

    storage = _storage(bucket, local_store)
    from jobd.services.metrics import RunMeter

    meter = RunMeter.start(
        kind="backfill",
        args={"limit": limit, "workers": workers},
        group_id=os.environ.get("JOBD_RUN_GROUP"),
    )
    with _connect() as conn:
        try:
            counts = backfill_envelopes(
                conn, storage, limit=limit, workers=workers, meter=meter
            )
        except Exception:
            meter.finish("failed")
            raise
        meter.finish("done")
    click.echo(
        f"seen {counts['seen']} | updated {counts['updated']}"
        f" | errors {counts['errors']}"
    )


@ingest.command("log")
@click.option("--account", help="Filter to one account.")
@click.option("-n", "limit", default=20, show_default=True, help="Rows to show.")
def ingest_log(account: str | None, limit: int) -> None:
    """Recent ingest runs — what a cron job would otherwise hide."""
    import psycopg

    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset.")

    query = (
        "SELECT started_at, finished_at, source, account, day, fetched, stored,"
        "  rows_inserted, failure, row_errors FROM ingest_run"
    )
    params: tuple[Any, ...] = ()
    if account:
        query += " WHERE account = %s"
        params = (account,)
    query += " ORDER BY started_at DESC LIMIT %s"
    params += (limit,)

    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        click.echo("No ingest runs recorded yet.")
        return

    for (
        started,
        finished,
        source,
        acct,
        day,
        fetched,
        stored,
        inserted,
        failure,
        row_errors,
    ) in rows:
        status = "RUNNING" if finished is None and failure is None else "ok"
        if failure:
            status = "FAILED"
        elif row_errors:
            status = f"ok, {row_errors.count(chr(10)) + 1} row error(s)"
        label = f"{source}:{acct}" + (f" day={day}" if day else "")
        click.echo(
            f"{started:%Y-%m-%d %H:%M:%S}  {label}  "
            f"fetched={fetched} stored={stored} rows={inserted}  {status}"
        )
        if failure:
            click.echo(f"    {failure}", err=True)


def _storage(bucket: str | None, local_store: Path | None) -> Any:
    """Pick a Storage adapter. S3 unless told otherwise.

    S3 is wrapped in `CachingStorage`, rooted at `~/.jobd/cache/raw` — every
    object `get()` fetches is mirrored to local disk, so a second read of the
    same message (fanout investigation, a reclassify, anything that re-reads
    headers) never leaves the machine again. Purely a speedup; S3 stays the
    source of truth and the cache directory is safe to delete at any time.
    """
    if bucket and local_store:
        raise click.ClickException("Give --bucket or --local-store, not both.")
    if local_store:
        from jobd.adapters.filesystem import FilesystemStorage

        return FilesystemStorage(local_store)
    if not bucket:
        raise click.ClickException(
            "No raw storage configured. Pass --bucket (or set JOBD_BUCKET), or "
            "--local-store for a filesystem archive."
        )
    from jobd.adapters.caching import CachingStorage
    from jobd.adapters.s3 import S3Storage

    cache_dir = Path.home() / ".jobd" / "cache" / "raw"
    return CachingStorage(S3Storage(bucket), cache_dir)


def _parse_since(since: str | None) -> datetime | None:
    if since is None:
        return None
    try:
        parsed = datetime.fromisoformat(since)
    except ValueError as exc:
        raise click.ClickException(f"--since {since!r} is not ISO-8601") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _report(result: Any, api_calls: int) -> None:
    click.echo(f"{result.account}:")
    click.echo(f"  fetched          {result.fetched}")
    click.echo(f"  stored (new)     {result.stored}")
    click.echo(f"  already stored   {result.already_stored}")
    click.echo(f"  rows inserted    {result.rows_inserted}")
    click.echo(f"  rows existing    {result.rows_existing}")
    click.echo(f"  api calls        {api_calls}")
    if result.cursor:
        click.echo(f"  cursor           {result.cursor}")
    for error in result.errors:
        click.echo(f"  ERROR {error}", err=True)


@ingest.command("linkedin")
def ingest_linkedin() -> None:
    """Import a LinkedIn data archive."""
    _not_yet("ingest linkedin")


@main.group()
def auth() -> None:
    """Connect a mail account. Your own OAuth client, never a shared one."""


@auth.command("gmail")
@click.option(
    "--client-secret",
    type=click.Path(path_type=Path),
    default=Path("~/.jobd/gmail_client_secret.json"),
    show_default=True,
    help="Desktop-app OAuth client JSON from your own Google Cloud project.",
)
@click.option("--port", default=8765, show_default=True, help="Loopback callback port.")
@click.option("--open-browser/--no-open-browser", default=False, show_default=True)
@click.option(
    "--manual",
    is_flag=True,
    help="No callback server: paste the redirect URL back yourself. Use when the "
    "port cannot be forwarded (plain SSH, bare docker run, remote host).",
)
def auth_gmail(
    client_secret: Path, port: int, open_browser: bool, manual: bool
) -> None:
    """Run the Gmail consent flow and store the token.

    In a container the browser runs on your host, so the callback port must be
    forwarded. Codespaces and the VS Code Remote extension forward it
    automatically once the flow starts listening. Where nothing forwards it,
    use --manual and carry the code back by hand.
    """
    from jobd.adapters.gmail import AuthError, run_wizard
    from jobd.adapters.secrets import TokenStore

    store = TokenStore()
    try:
        account = run_wizard(
            client_secret.expanduser(),
            store,
            port=port,
            open_browser=open_browser,
            manual=manual,
            echo=click.echo,
        )
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Connected {account}. Token stored in {store.backend}.")


@auth.command("status")
def auth_status() -> None:
    """Show where tokens live, and which accounts are connected."""
    from jobd.adapters.secrets import TokenStore

    store = TokenStore()
    click.echo(f"backend: {store.backend}")
    if not store.is_os_keyring:
        click.echo("warning: no OS keyring here — see SECURITY.md §3", err=True)
    accounts = store.accounts()
    click.echo("accounts: " + (", ".join(accounts) if accounts else "(none listed)"))


def _repos(conn: Any) -> Any:
    """The repository bundle. See jobd.adapters.postgres.repositories."""
    from jobd.adapters.postgres import repositories

    return repositories(conn)


def _connect() -> Any:
    import psycopg

    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset.")
    return psycopg.connect(settings.database_url)


def _llm(model: str) -> Any:
    from jobd.adapters.llm import load_provider

    return load_provider(model)


@main.command()
@click.option(
    "--model",
    default="default",
    show_default=True,
    envvar="JOBD_MODEL",
    help="ollama/<model> | any LiteLLM id. 'default' resolves JOBD_MODEL, "
    "then the built-in default. Only cloud ids leave the machine.",
)
@click.option("--limit", default=1000, show_default=True, help="Batch size.")
@click.option("--all", "run_all", is_flag=True, help="Keep going until none remain.")
@click.option("--embed", is_flag=True, help="Also compute embeddings (doubles calls).")
@click.option(
    "--reclassify",
    is_flag=True,
    help="Re-run classification over every already-classified message too, "
    "not just new ones — e.g. after switching --model. Appends: derived "
    "companies/applications/stage events are re-derived and merged (upsert), "
    "never truncated. See repositories.py's clear_classification / "
    "stage_event.add docstrings.",
)
@click.option(
    "--escalation-model",
    default=None,
    help="Second LiteLLM id, tried only when --model comes back ambiguous "
    "(the same case that would otherwise go straight to the review queue) — "
    "e.g. openrouter/google/gemini-3.7-flash. Off by default: no second call.",
)
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket holding raw messages.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Filesystem archive."
)
@click.option(
    "--workers",
    default=32,
    show_default=True,
    help="Thread-pool size for the per-batch S3 prefetch. The fetch is "
    "I/O-bound, so this is the knob to raise for a bigger box / more "
    "bandwidth, or lower for a constrained one.",
)
@click.option(
    "--llm-workers",
    default=8,
    show_default=True,
    help="Concurrent LLM extraction calls per batch. The model call is the "
    "long pole (a network round trip), so a small pool multiplies classify "
    "throughput; DB writes and the learning policy stay sequential regardless.",
)
@click.option(
    "--partition",
    default=None,
    help="k/N — process only the k-th of N disjoint hash slices of the "
    "mailbox, so N classify processes run concurrently without racing. "
    "Launch one process per slice (k = 0..N-1).",
)
def classify(
    model: str,
    limit: int,
    run_all: bool,
    embed: bool,
    reclassify: bool,
    escalation_model: str | None,
    bucket: str | None,
    local_store: Path | None,
    workers: int,
    llm_workers: int,
    partition: str | None,
) -> None:
    """Turn stored messages into an evidence-linked record.

    The metadata pre-filter runs before any model call, so on a cloud
    model the `filtered out` count is the number of messages that did *not*
    leave your machine (I4). Messages a learned rule or a resolved thread
    settles never reach a model either; only the genuinely undecided
    residue does — and every confident answer teaches a rule that shrinks
    that residue for the next run (see `jobd.domain.learning`).
    """
    from jobd.services import ClassifyResult, classify_pending
    from jobd.services.classify import mirror_classify
    from jobd.services.rebuild import _accumulate

    storage = _storage(bucket, local_store)
    slice_of: tuple[int, int] | None = None
    if partition:
        try:
            k_str, n_str = partition.split("/", 1)
            slice_of = (int(k_str), int(n_str))
        except ValueError as exc:
            raise click.UsageError("--partition takes k/N, e.g. 3/8.") from exc
        if not 0 <= slice_of[0] < slice_of[1]:
            raise click.UsageError("--partition k must be in [0, N).")
    provider = _llm(model)
    esc_provider = _llm(escalation_model) if escalation_model else None
    totals = ClassifyResult()

    from jobd.adapters.llm.credits import CreditGuard, CreditsLow

    guard = CreditGuard()
    if provider.name.startswith("openrouter/"):
        # Fail before burning anything — a 402 halfway through a mailbox is
        # half a run's spend for nothing (live-caught on the first audit).
        try:
            guard.require()
        except CreditsLow as exc:
            raise click.ClickException(str(exc)) from None

    with _connect() as conn:
        repos = _repos(conn)
        if reclassify:
            total_cleared = 0
            while True:
                n = repos.messages.clear_classification(limit=2000, partition=slice_of)
                conn.commit()
                total_cleared += n
                if run_all and n:
                    click.echo(f"...cleared {total_cleared}", err=True)
                if n == 0:
                    break
            click.echo(
                f"cleared classification on {total_cleared} messages "
                "(companies/applications/stage events kept, not truncated)"
            )
        from jobd.services.metrics import RunMeter

        count_clause = "WHERE classified_at IS NULL"
        count_params: tuple[object, ...] = ()
        if slice_of is not None:
            count_clause += " AND abs(hashtext(id::text)) %% %s = %s"
            count_params = (slice_of[1], slice_of[0])
        run_total = conn.execute(
            f"SELECT count(*) FROM message {count_clause}", count_params
        ).fetchone()[0]
        meter = RunMeter.start(
            kind="classify",
            total=int(run_total) if run_all else min(int(run_total), limit),
            args={"model": model, "limit": limit, "all": run_all},
            worker=partition or "",
            group_id=os.environ.get("JOBD_RUN_GROUP"),
            providers=[p for p in (provider, esc_provider) if p is not None],
        )
        try:
            while True:
                batch = classify_pending(
                    storage=storage,
                    llm=provider,
                    escalation_llm=esc_provider,
                    repos=repos,
                    conn=conn,
                    limit=limit,
                    embed=embed,
                    fetch_workers=workers,
                    llm_workers=llm_workers,
                    partition=slice_of,
                    meter=meter,
                )
                _accumulate(totals, batch)
                mirror_classify(meter, totals)
                balance = guard.balance()
                if balance is not None:
                    meter.set_counter("credits_left", round(balance, 2))
                if not guard.ok():
                    # Stop at the batch boundary, cleanly: everything so far
                    # is committed, the run row says why, and the next
                    # `--all` resumes exactly where this stopped.
                    click.echo(
                        f"stopping: OpenRouter balance ${balance:.2f} under "
                        f"the ${guard.floor:.2f} floor — resume after "
                        "topping up at https://openrouter.ai/settings/credits",
                        err=True,
                    )
                    meter.finish("low-credits")
                    break
                if run_all and batch.seen:
                    click.echo(
                        f"...{totals.seen} seen"
                        f" | {totals.filtered_out} negative"
                        f" | {totals.carried_forward} thread-carry(free)"
                        f" | {totals.llm_calls} model"
                        f" | {totals.escalated} escalated"
                        f" | {totals.recorded} recorded"
                        f" | {totals.queued_for_review} queued"
                        f" | {totals.rules_learned} rules-learned"
                        f" | {len(totals.errors)} errors",
                        err=True,
                    )
                if not run_all or batch.seen == 0:
                    break
        except Exception:
            meter.finish("failed")
            raise
        meter.finish("done")

    click.echo(f"model            {provider.name} (local={provider.is_local})")
    click.echo(f"  seen           {totals.seen}")
    click.echo(f"  filtered out   {totals.filtered_out}   (negative — never sent)")
    click.echo(
        f"  thread-carry   {totals.carried_forward}   (positive — same thread, free)"
    )
    click.echo(f"  model calls    {totals.llm_calls}   (undecided)")
    if esc_provider is not None:
        click.echo(
            f"  escalated      {totals.escalated}   (2nd opinion, {esc_provider.name})"
        )
    click.echo(f"  not job-related{totals.not_job_related:>4}")
    click.echo(f"  queued         {totals.queued_for_review}")
    click.echo(f"  recorded       {totals.recorded}")
    click.echo(f"  companies      +{totals.companies_created}")
    click.echo(f"  applications   +{totals.applications_created}")
    click.echo(f"  stage events   +{totals.stage_events}")
    click.echo(
        f"  rules learned  +{totals.rules_learned}   (auto — see `rank`)"
    )
    click.echo(
        f"  rules demoted  +{totals.rules_demoted}   (exploration found them wrong)"
    )
    click.echo(
        f"  explored       {totals.explored}   (negatives re-checked via the model)"
    )
    if esc_provider is not None:
        click.echo(
            f"  terminal-routed{totals.terminal_routed:>3}   (offer/rejection -> strong model)"
        )
    click.echo(
        f"  stage conflicts{totals.stage_conflicts:>3}   (non-terminal after terminal, skipped)"
    )
    click.echo(
        f"  thread-reuse   {totals.thread_llm_reused}   (free — thread already read)"
    )
    for error in totals.errors[:10]:
        click.echo(f"  ERROR {error}", err=True)


@main.command("embed-backfill")
@click.option(
    "--model",
    default=None,
    envvar="JOBD_MODEL",
    help="LiteLLM id whose .embed() to call. Defaults to JOBD_MODEL, then "
    "JOBD_CHAT_MODEL. Providers without a real embedder raise clearly "
    "rather than writing zero vectors.",
)
@click.option("--limit", default=2000, show_default=True, help="Messages per call.")
@click.option(
    "--batch-size", default=100, show_default=True, help="Texts per embed() call."
)
@click.option("--all", "run_all", is_flag=True, help="Keep going until none remain.")
def embed_backfill(
    model: str | None, limit: int, batch_size: int, run_all: bool
) -> None:
    """Embed already-recorded messages that predate `classify --embed`.

    One-off catch-up, not part of the steady-state pipeline: `--embed` only
    ever fires at a message's first classification, so anything classified
    before that flag existed (or without it) has no embedding. Semantic
    search (`search_communications`, mode="semantic", in the chat panel)
    can only find what this has actually embedded — run it once, then rely
    on `classify --embed` to keep new mail covered going forward.
    """
    from jobd.services.classify import backfill_embeddings

    settings = load_settings()
    chosen = model or settings.chat_model
    if not chosen:
        raise click.ClickException(
            "No model configured — pass --model or set JOBD_MODEL/JOBD_CHAT_MODEL."
        )
    provider = _llm(chosen)
    with _connect() as conn:
        repos = _repos(conn)
        total = 0
        while True:
            n = backfill_embeddings(
                llm=provider, repos=repos, conn=conn, limit=limit, batch_size=batch_size
            )
            total += n
            if run_all and n:
                click.echo(f"...{total} embedded", err=True)
            if not run_all or n == 0:
                break
    click.echo(f"embedded {total} messages ({provider.name})")


@main.command("rank")
@click.option("--limit", default=30, show_default=True)
def rank_correspondents(limit: int) -> None:
    """Highest-leverage unresolved senders — the fanout starting point.

    One row can be a whole company domain or one exact address (personal-mail
    providers are grouped by address, see `top_unresolved_correspondents`'s
    docstring). `count` is how many still-unclassified messages resolving
    this one attribute would settle at once.
    """
    with _connect() as conn:
        repos = _repos(conn)
        rows = repos.messages.top_unresolved_correspondents(limit)
    for r in rows:
        click.echo(f"{r['count']:>6}  [{r['kind']:>7}]  {r['key']}")
        click.echo(f"          {r['sample_subject'][:100]}")


@main.command("learn")
@click.option("--domain", help="Match every sender at this domain.")
@click.option("--address", help="Match this exact sender/recipient address.")
@click.option(
    "--verdict",
    type=click.Choice(["positive", "negative", "undecided"]),
    default="positive",
    show_default=True,
    help="undecided pins this attribute to the LLM path — an explicit "
    "override protecting it from the bulk-header/Gmail-label negative "
    "tier (see prefilter.LearnedVerdict's docstring).",
)
@click.option("--company", help="Company name, for a positive rule.")
@click.option(
    "--company-domain", help="Company web domain, if different from --domain."
)
@click.option(
    "--category",
    help="Tag under a named group (e.g. LINKEDIN_JOB_ALERTS, see "
    "prefilter.SenderCategory) instead of a one-off verdict. Re-running "
    "--category with a different --verdict changes it for every sender "
    "ever tagged with it, not just this one.",
)
@click.option(
    "--kind",
    type=click.Choice(["employer", "agency"]),
    default="employer",
    show_default=True,
    help="Whether --company is a real employer or a recruiting agency "
    "(migration 0012). Only matters with --verdict positive, when a new "
    "company row gets created.",
)
def learn(
    domain: str | None,
    address: str | None,
    verdict: str,
    company: str | None,
    company_domain: str | None,
    category: str | None,
    kind: str,
) -> None:
    """Teach one rule and fan it out over the current backlog immediately.

    The two-part mechanism from classify.py's module docstring: `sender_rule`
    (so every message ingested from now on inherits the verdict for free via
    prefilter.py's `learned`) plus a bulk UPDATE over every still-unclassified
    message that already has this attribute (so the backlog benefits right
    now, not just on its next re-ingest) — skipped for `undecided`, since
    there is nothing to write into the record; it only ever routes onward.

    The mechanism itself lives in `services/learning.py`, so the dashboard's
    teach form and this command are the same code rather than two
    implementations that agree until they don't.
    """
    from jobd.services.learning import LearnError, learn_rule

    with _connect() as conn:
        try:
            result = learn_rule(
                conn,
                _repos(conn),
                domain=domain,
                address=address,
                verdict=verdict,  # type: ignore[arg-type]
                company=company,
                company_domain=company_domain,
                category=category,
                kind=kind,
            )
        except LearnError as error:
            raise click.ClickException(str(error)) from error

    click.echo(
        f"learned {result.match_type}={result.value} -> {result.verdict},"
        f" resolved {result.resolved} backlog messages"
    )


@main.group()
def review() -> None:
    """The queue of extractions the model was not sure about."""


@review.command("list")
@click.option("--limit", default=20, show_default=True)
def review_list(limit: int) -> None:
    """Show pending items. Nothing here has touched the record."""
    with _connect() as conn:
        items = _repos(conn).reviews.pending(limit)
    if not items:
        click.echo("Queue empty.")
        return
    for item in items:
        extraction = item["extraction"]
        fanout = item.get("fanout", 0)
        click.echo(
            f"{item['id']}  label={extraction.get('label')}  {item['reason']}"
            + (f"  [{fanout} unresolved]" if fanout else "")
            + "\n"
            f"    company={extraction.get('company_name')!r}"
            f" role={extraction.get('role_title')!r}"
            f" stage={extraction.get('stage')!r}"
            f"  msg={item['message_id']}"
        )


@review.command("resolve")
@click.argument("review_id")
@click.option(
    "--approve/--reject",
    required=True,
    help="Approving records the human decision; it does not yet write the record.",
)
def review_resolve(review_id: str, approve: bool) -> None:
    """Close a queue item.

    Approving marks the decision. Writing an approved extraction into the
    record is post-v1: doing it here would mean a second, differently-shaped
    write path into the same tables, and the one in `classify` is the one under
    test.
    """

    with _connect() as conn:
        decision = "approved" if approve else "rejected"
        _repos(conn).reviews.resolve(UUID(review_id), decision)
        conn.commit()
    click.echo("approved" if approve else "rejected")


@review.command("sweep")
@click.option(
    "--model",
    default=None,
    help="Judge model (litellm id). Defaults to JOBD_JUDGE_MODEL.",
)
@click.option("--limit", default=500, show_default=True)
@click.option(
    "--fresh",
    is_flag=True,
    help="Re-judge even threads with a stored reading. The queue's cached "
    "readings are punts by definition, so a cache hit can only punt again; "
    "this pays for a second opinion and the upsert replaces the row.",
)
def review_sweep(model: str | None, limit: int, fresh: bool) -> None:
    """Re-judge the pending queue with the strong model and apply verdicts.

    The queue is exactly what the classify-time model punted on; a human
    clearing it by hand is doing model work. This hands each item to the
    judge (the escalation role, applied late): clear negatives resolve,
    positives with a named company get recorded through the normal path,
    and only what the judge also punts on stays for a human.
    """
    import os

    from jobd.services.classify import sweep_review_queue

    model_id = model or os.environ.get("JOBD_JUDGE_MODEL")
    if not model_id:
        raise click.UsageError("Pass --model or set JOBD_JUDGE_MODEL.")
    judge = _llm(model_id)
    if model_id.startswith("openrouter/"):
        from jobd.adapters.llm.credits import CreditGuard, CreditsLow

        try:
            CreditGuard().require()
        except CreditsLow as exc:
            raise click.ClickException(str(exc)) from None
    from jobd.services.metrics import RunMeter

    meter = RunMeter.start(
        kind="sweep",
        args={"model": model_id, "limit": limit, "fresh": fresh},
        group_id=os.environ.get("JOBD_RUN_GROUP"),
        providers=[judge],
    )
    with _connect() as conn:
        try:
            result = sweep_review_queue(
                judge=judge, repos=_repos(conn), conn=conn, limit=limit,
                fresh=fresh, meter=meter,
            )
        except Exception:
            meter.finish("failed")
            raise
        meter.finish("done")
    click.echo(f"judge            {judge.name}")
    click.echo(f"  seen           {result.seen}")
    click.echo(f"  not job-related{result.not_job_related:>4}   (resolved)")
    click.echo(f"  recorded       {result.recorded}   (company/application written)")
    click.echo(f"  thread-carry   {result.thread_carried}   (free — thread already resolved)")
    click.echo(f"  prefiltered    {result.prefiltered}   (free — metadata negative)")
    click.echo(f"  domain-carry   {result.domain_carried}   (free — known company domain)")
    click.echo(f"  thread-reuse   {result.thread_reused}   (free — thread already read)")
    click.echo(f"  no entity      {result.no_entity}   (anonymized pitch, closed)")
    click.echo(f"  still queued   {result.still_queued}   (the human's actual job)")
    click.echo(f"  rules learned  {result.rules_learned}")
    if result.errors:
        click.echo(f"  errors         {len(result.errors)}")
        for line in result.errors[:5]:
            click.echo(f"    {line}")
    repeats = {d: n for d, n in result.rejected_domains.items() if n >= 3}
    if repeats:
        click.echo("rejected 3+ times — teach a negative rule to keep them out:")
        for domain, n in sorted(repeats.items(), key=lambda kv: -kv[1]):
            click.echo(f"  {n:>3}x  jobd learn --domain {domain} --verdict negative")


@main.command()
@click.option(
    "--local-store",
    type=click.Path(path_type=Path),
    envvar="JOBD_LOCAL_STORE",
    default=Path.home() / ".jobd" / "demo-raw",
    show_default=True,
    help="Filesystem raw store for the synthetic mail (no S3 needed).",
)
@click.pass_context
def demo(ctx: click.Context, local_store: Path) -> None:
    """Seed a synthetic job search and classify it — a working dashboard
    with zero credentials.

    One believable search (offers, rejections, a ghosting, an agency, bulk
    noise) is ingested through the real pipeline into a filesystem raw
    store, then classified with the configured model (JOBD_MODEL or the
    default — needs its API key, e.g. OPENROUTER_API_KEY; the old keyless
    rule-based tier was removed when classification rules went dynamic).
    Safe on a fresh database; refuses one that already holds the demo
    account.
    """
    from jobd.services.demo import DEMO_ACCOUNT, seed_demo

    with _connect() as conn:
        existing = conn.execute(
            "SELECT count(*) FROM message WHERE account = %s", (DEMO_ACCOUNT,)
        ).fetchone()
        if existing and existing[0]:
            raise click.ClickException(
                f"{existing[0]} demo messages already ingested — the demo "
                "seeds once per database. Start from a fresh database to "
                "re-seed."
            )
        counts = seed_demo(
            storage=_storage(None, local_store),
            messages=_repos(conn).messages,
            conn=conn,
        )
    click.echo(
        f"seeded {counts['rows']} messages ({counts['errors']} errors) — "
        "classifying..."
    )
    ctx.invoke(
        classify,
        model="default",
        limit=1000,
        run_all=True,
        embed=False,
        reclassify=False,
        escalation_model=None,
        bucket=None,
        local_store=local_store,
        workers=8,
        partition=None,
    )
    click.echo("demo ready — start the dashboard and open it.")


@main.command()
@click.argument("company")
@click.option("--ghosted-after", default=21, show_default=True, help="Days of silence.")
def timeline(company: str, ghosted_after: int) -> None:
    """Print a company's reconstructed timeline, with evidence links."""
    from jobd.services import timeline as build_timeline

    with _connect() as conn:
        company_id = build_timeline.find_company(conn, company)
        if company_id is None:
            raise click.ClickException(f"No company matching {company!r}.")
        built = build_timeline.build(conn, company_id)
    click.echo(build_timeline.render(built))


@main.command()
def companies() -> None:
    """Every company discovered in your mail. Never user-created (P4.1)."""
    with _connect() as conn:
        found = _repos(conn).companies.all()
    if not found:
        click.echo("No companies yet. Run `jobd classify`.")
        return
    for entry in found:
        click.echo(
            f"{entry.last_seen_at:%Y-%m-%d}  {entry.canonical_name}"
            + (f"  ({entry.domain})" if entry.domain else "")
        )


@main.command("dedupe")
@click.option("--limit", default=50, show_default=True, help="Candidate pairs to show.")
def dedupe(limit: int) -> None:
    """Propose duplicate companies for a human to join — never applies them.

    Entity resolution biases toward *split*, and this is the tool that pays
    that debt back: it ranks pairs whose name/domain make them plausibly the
    same employer. Confirm one with `jobd learn` (or the teach form) — adding
    the alias a human reads is the merge; nothing here writes on its own.
    """
    from jobd.services.dedupe import suggest_merges

    with _connect() as conn:
        companies_ = _repos(conn).companies.all()
        candidates = suggest_merges(companies_, limit=limit)
    if not candidates:
        click.echo("No likely duplicates found.")
        return
    for c in candidates:
        click.echo(
            f"{c.score:.2f}  {c.left_name!r}  <->  {c.right_name!r}  [{c.reason}]"
        )



@main.command("verify")
@click.option(
    "--model",
    default="default",
    show_default=True,
    envvar="JOBD_MODEL",
    help="A model id — e.g. openrouter/anthropic/claude-3.5-haiku, or any "
    "other LiteLLM id. 'default' resolves JOBD_MODEL, then the built-in "
    "default.",
)
@click.option(
    "--limit", default=20, show_default=True, help="Companies to audit this run."
)
@click.option(
    "--company", help="Only this one company — a UUID, domain, alias, or "
    "canonical name."
)
@click.option(
    "--reverify", is_flag=True, help="Re-audit companies that already have a pass."
)
@click.option(
    "--concurrency",
    default=6,
    show_default=True,
    help="Model calls in flight at once (network-bound, so real parallelism "
    "even in Python — see verify_companies' docstring). Database writes stay "
    "sequential regardless. 1 for a plain one-at-a-time run.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Write every suggested sender_rule immediately (source=auto) — "
    "positive, undecided, AND negative (this pass read the whole chain, not "
    "one message, so its verdict is a stronger claim than _record's online "
    "learning ever makes — see verify.py's docstring). When the chain is "
    "confirmed NOT job-related: every message it touched is labelled "
    "negative directly (no resweep needed — this pass already read them) "
    "and the company row itself is deleted. When it IS job-related but the "
    "name/kind differ from what's on record, those are corrected instead.",
)
def verify(
    model: str,
    limit: int,
    company: str | None,
    reverify: bool,
    concurrency: int,
    do_apply: bool,
) -> None:
    """Audit companies' whole message chains with a real model.

    Independent of `classify` (PRD's own "verify the findings so far" ask):
    reads everything already linked to one company at a time — every message,
    not just one — and asks the model whether the entity is genuinely job
    related, what it should be called, and what deterministic sender_rule
    candidates a human (or --apply) can act on. Costs one model call per
    company, not per message; findings are stored in `company_verification`
    even without --apply, and a company page shows its most recent pass.
    """
    from jobd.services import timeline as build_timeline
    from jobd.services.verify import VerifyOutcome, verify_companies

    provider = _llm(model)

    def _report(outcome: VerifyOutcome) -> None:
        # Streamed per company, not batched at the end: on a sweep that
        # takes long enough to hit a timeout/Ctrl-C/crash, this is the only
        # record of what happened to companies processed before that point
        # — see verify_companies' `after_each` docstring on why this same
        # callback is also what commits.
        if outcome.error:
            click.echo(f"{outcome.company.canonical_name}  [ERROR] {outcome.error}")
            return
        result = outcome.result
        assert result is not None
        mark = "OK" if result.classification_correct else "NOT JOB-RELATED"
        click.echo(f"{outcome.company.canonical_name}  [{mark}]")
        if outcome.company_deleted:
            click.echo(
                f"  removed — {outcome.messages_marked_negative} message(s)"
                " labelled negative"
            )
        if outcome.identity_updated:
            click.echo(
                f"  identity corrected -> {result.company_name!r} ({result.kind})"
            )
        elif result.company_name != outcome.company.canonical_name:
            click.echo(f"  name?     model says {result.company_name!r}")
        if not outcome.identity_updated and result.kind != outcome.company.kind:
            click.echo(f"  kind?     model says {result.kind!r}")
        if not result.classification_correct and result.misclassification_notes:
            click.echo(f"  issue:    {result.misclassification_notes}")
        for rule in result.rules:
            applied = rule in outcome.applied_rules
            tag = "applied" if applied else "suggested"
            click.echo(
                f"  rule ({tag}): {rule.match_type}={rule.value}"
                f" -> {rule.verdict}  ({rule.reason})"
            )

    with _connect() as conn:
        repos = _repos(conn)
        company_id = None
        if company:
            try:
                company_id = UUID(company)
            except ValueError:
                company_id = build_timeline.find_company(conn, company)
            if company_id is None:
                raise click.ClickException(f"No company matching {company!r}.")

        def _commit_and_report(outcome: VerifyOutcome) -> None:
            conn.commit()
            _report(outcome)

        outcomes = verify_companies(
            llm=provider,
            companies=repos.companies,
            messages=repos.messages,
            verifications=repos.verifications,
            sender_rules=repos.sender_rules,
            limit=limit,
            company_id=company_id,
            reverify=reverify,
            apply=do_apply,
            after_each=_commit_and_report,
            concurrency=concurrency,
        )

    if not outcomes:
        click.echo("Nothing to verify (no companies, or all already audited — "
                    "try --reverify).")
        return

    errors = [o for o in outcomes if o.error]
    if errors:
        click.echo(
            f"\n{len(errors)}/{len(outcomes)} company/companies errored — "
            "already committed results are unaffected, re-run to retry them "
            "(already-verified companies are skipped unless --reverify)."
        )


@main.command("logos")
@click.option("--limit", default=500, show_default=True, help="Domains per run.")
@click.option(
    "--refresh",
    is_flag=True,
    help="Re-ask about every domain, including the ones that had nothing.",
)
@click.option(
    "--workers", default=8, show_default=True, help="Concurrent HTTP fetches."
)
def logos(limit: int, refresh: bool, workers: int) -> None:
    """Cache a logo for every company that has a domain.

    THIS COMMAND REACHES THE NETWORK. It is the only part of the dashboard's
    logo feature that does, and that is the point: the browser never talks to
    a logo CDN, because doing so would hand every company in your job search
    to that CDN on every page view. This asks once per domain, from your
    machine, and stores the bytes in Postgres.

    What each request carries is one domain you already correspond with —
    no message content, no names. Clearbit is asked first (real brand marks),
    then DuckDuckGo's favicon proxy. A domain neither has is remembered as a
    miss so the next run does not ask again; `--refresh` overrides that.
    """
    from jobd.services import logos as logo_service

    with _connect() as conn:
        result = logo_service.backfill(
            conn, limit=limit, refresh=refresh, workers=workers
        )
        after = logo_service.coverage(conn)
    click.echo(
        f"asked {result['asked']} · found {result['found']} · "
        f"nothing {result['missing']}"
    )
    click.echo(
        f"{after['found']} of {after['with_domain']} companies with a domain now have "
        f"a logo ({after['companies']} companies in the record)"
    )


@main.command()
@click.option(
    "--account",
    "accounts",
    multiple=True,
    help="Mailbox to scrape; repeat for several. Defaults to every account "
    "the token store knows.",
)
@click.option(
    "--window",
    "window_days",
    type=int,
    default=None,
    help="Only look at mail newer than this many days — the daily-cron mode. "
    "Omit for the full backfill.",
)
@click.option(
    "--model",
    envvar="JOBD_MODEL",
    default="default",
    show_default=True,
    help="Extractor for messages the free tiers can't settle. 'default' "
    "resolves JOBD_MODEL, then the built-in default; ollama/<model> runs "
    "fully local.",
)
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket for raw messages.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Filesystem archive."
)
def scrape(
    accounts: tuple[str, ...],
    window_days: int | None,
    model: str,
    bucket: str | None,
    local_store: Path | None,
) -> None:
    """Seed-and-expand scrape: find the job mail without sweeping the mailbox.

    Seed queries (sent threads, ATS senders, the InMail relay, a phrase pack)
    pull the high-signal slices; every confident positive teaches new sender
    domains/addresses/threads whose queries expand the frontier until it is
    empty; a final residual pass triages direct-addressed mail nothing else
    matched. Fully idempotent — running it every day is the intended use,
    and overlap with previous runs costs one cheap listing pass, not a
    re-download. Watch it live on the dashboard's /scrape page when started
    from there instead.
    """
    from jobd.scrape.service import ScrapeConfigError, run_scrape

    resolved = list(accounts)
    if not resolved:
        from jobd.adapters.gmail import GmailSource
        from jobd.adapters.secrets import TokenStore

        resolved = GmailSource(TokenStore()).accounts()
    if not resolved:
        raise click.ClickException(
            "No accounts. Run `jobd auth gmail --account you@example.test` "
            "first, or pass --account."
        )

    def sink(event: Any) -> None:
        data = event.data
        if event.kind == "run_started":
            click.echo(
                f"scrape: {', '.join(data['accounts'])} · "
                + (f"last {data['window_days']}d" if data["window_days"] else "full")
                + f" · {data['seed_queries']} seed queries"
            )
        elif event.kind == "query_yield" and data["new"]:
            click.echo(
                f"  [{data['origin']}] +{data['new']} new "
                f"({data['matched']} matched)"
            )
        elif event.kind == "fetch_progress":
            click.echo(f"  fetched {data['fetched']}/{data['total']}", nl=False)
            click.echo("\r", nl=False)
        elif event.kind == "expansion":
            click.echo(
                f"  hop {data['hop']}: {data['new_domains']} domains, "
                f"{data['new_addresses']} addresses, {data['new_threads']} "
                f"threads -> {data['queries']} queries"
            )
        elif event.kind == "fact":
            click.echo(f"  * {data['text']}")
        elif event.kind == "done":
            click.echo("done.")

    try:
        counters = run_scrape(
            accounts=resolved,
            window_days=window_days,
            model=model,
            bucket=bucket,
            local_store=local_store,
            sink=sink,
        )
    except ScrapeConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    for key in (
        "queries_run", "listed", "listed_known", "fetched", "ingested",
        "classified", "recorded", "filtered_out", "llm_calls",
        "queued_for_review", "rules_learned", "api_calls",
    ):
        if key in counters:
            click.echo(f"  {key:16s} {counters[key]}")


@main.command()
@click.option(
    "--model",
    envvar="JOBD_MODEL",
    default="default",
    show_default=True,
    help="A model id (e.g. openrouter/google/gemini-2.5-flash-lite) — "
    "the audit is the model's judgment. 'default' resolves JOBD_MODEL, "
    "then the built-in default.",
)
@click.option("--company", "company_name", default=None, help="Limit to one company.")
@click.option(
    "--judge-model",
    envvar="JOBD_JUDGE_MODEL",
    default=None,
    help="Stronger model for the application audit; defaults to --model.",
)
@click.option(
    "--apply/--dry-run",
    "apply_",
    default=False,
    show_default=True,
    help="Apply the plan, or just print it.",
)
@click.option(
    "--workers",
    default=8,
    show_default=True,
    help="Concurrent model calls. DB writes stay serial regardless.",
)
def audit(
    model: str,
    company_name: str | None,
    judge_model: str | None,
    apply_: bool,
    workers: int,
) -> None:
    """LLM audit of the record: unlink misclassified mail, merge duplicate
    applications, teach corrective sender rules. See scrape/audit.py for the
    live cases this repairs (package-delivery mail recorded as job mail, a
    post-acceptance engagement split into two applications)."""
    from jobd.adapters.llm.credits import CreditGuard, CreditsLow
    from jobd.scrape.audit import run_audit

    credit_guard = CreditGuard()
    if model.startswith("openrouter/"):
        try:
            credit_guard.require()
        except CreditsLow as exc:
            raise click.ClickException(str(exc)) from None
    from jobd.adapters.llm import load_provider as _load

    # Same cap the judge gets: a 30-message batch's verdict array in
    # pretty-printed JSON routinely passes 2048 output tokens — live-caught
    # as JSONDecodeError mid-array (a truncated list, not a malformed one).
    provider = _load(model, max_tokens=8192)
    with _connect() as conn:
        ids = None
        if company_name:
            rows = conn.execute(
                "SELECT id FROM company WHERE canonical_name ILIKE %s",
                (f"%{company_name}%",),
            ).fetchall()
            if not rows:
                raise click.ClickException(f"No company matching {company_name!r}.")
            ids = [r[0] for r in rows]
        from jobd.adapters.llm import load_provider

        # Bigger cap than extraction's default: an agency company can carry a
        # dozen applications and the judge's action list truncated at 2048
        # (live-caught as finish_reason='length' errors). Reasoning on: the
        # judge's calls are structural judgments (merge two applications,
        # delete a company) where flash without thinking kept title variants
        # of one process apart — worth thinking tokens on ~700 calls in a
        # way bulk extraction never is.
        judge = (
            load_provider(judge_model, max_tokens=8192, reasoning_effort="medium")
            if judge_model
            else None
        )
        from jobd.services.metrics import RunMeter

        meter = RunMeter.start(
            kind="audit",
            args={
                "model": model,
                "judge": judge_model,
                "apply": apply_,
                "company": company_name,
            },
            group_id=os.environ.get("JOBD_RUN_GROUP"),
            providers=[p for p in (provider, judge) if p is not None],
        )
        try:
            result = run_audit(
                conn, provider, judge=judge, apply=apply_, company_ids=ids,
                meter=meter, workers=workers, credit_guard=credit_guard,
            )
        except Exception:
            meter.finish("failed")
            raise
        meter.finish("done")

    mode = "applied" if apply_ else "plan (dry-run — pass --apply to execute)"
    click.echo(f"audit {mode}:")
    for company, action, detail in result.plan:
        click.echo(f"  {company:28.28s} {action:7s} {detail}")
    click.echo(
        f"companies {result.companies_checked} · messages {result.messages_checked}"
        f" · unlinked {result.messages_unlinked}"
        f" · stage events removed {result.stage_events_removed}"
        f" · apps merged {result.applications_merged}"
        f" · closed {result.applications_closed}"
        f" · companies -{result.companies_deleted}"
        f"/~{result.companies_renamed}/merged {result.companies_merged}"
        f" · rules {result.rules_taught}+{result.rules_demoted} demoted"
        f" · model calls {result.llm_calls}"
    )
    for error in result.errors:
        click.echo(f"  ERROR {error}", err=True)


@main.command()
@click.option(
    "--model",
    envvar="JOBD_DISTILL_MODEL",
    default="openrouter/z-ai/glm-5.2",
    show_default=True,
    help="Reasoning-capable LiteLLM id that judges the rule candidates. "
    "Deliberately a bigger head than the extraction tier: rule-making is "
    "one cheap batch call whose mistakes compound across every future "
    "message, so it gets the strongest judgment available.",
)
@click.option(
    "--apply/--dry-run",
    "apply_",
    default=False,
    show_default=True,
    help="Teach the surviving rules, or just print them.",
)
def distill(model: str, apply_: bool) -> None:
    """Compile the model's past judgments into deterministic sender rules.

    Sends per-domain aggregates only — counts, label distributions, stored
    reasoning snips. Never re-sends any message content the model has
    already read. Rules land as source='distilled', capped at
    negative/undecided; the verify sweep stays the demotion path.
    """
    from jobd.adapters.llm import load_provider
    from jobd.services.distill import distill_rules
    from jobd.services.metrics import RunMeter

    llm = load_provider(model, max_tokens=8192, reasoning_effort="medium")
    meter = RunMeter.start(
        kind="distill", args={"model": model, "apply": apply_}, providers=[llm]
    )
    with _connect() as conn:
        own = conn.execute(
            "SELECT account FROM message WHERE account IS NOT NULL LIMIT 1"
        ).fetchone()
        own_address = (own[0] if own else "") or ""
        try:
            result = distill_rules(
                conn, llm, own_address=own_address, apply=apply_, meter=meter
            )
        except Exception:
            meter.finish("failed")
            raise
        meter.finish("done")
    mode = "taught" if apply_ else "would teach (dry-run — pass --apply)"
    click.echo(f"candidates {result.candidates} · calls {result.llm_calls}"
               f" · skipped/vetoed {result.skipped}")
    click.echo(f"{mode}:")
    for domain, verdict, reason in result.taught:
        click.echo(f"  {domain:32.32s} {verdict:10s} {reason}")
    for error in result.errors:
        click.echo(f"  ERROR {error}", err=True)


@main.command()
@click.option("--limit", default=20, show_default=True)
@click.option(
    "--watch",
    is_flag=True,
    help="Redraw every 2s until interrupted — a terminal twin of the Runs page.",
)
def runs(limit: int, watch: bool) -> None:
    """Live pipeline runs: progress, rate, ETA, spend, stalls.

    Reads the `pipeline_run` rows every long job keeps current — the same
    source the dashboard's Runs page polls, so both always agree.
    """
    import time as _time

    from jobd.services.metrics import read_runs

    def _fmt_eta(seconds: int | None) -> str:
        if seconds is None:
            return "-"
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m{seconds % 60:02d}s"
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"

    def _draw() -> None:
        with _connect() as conn:
            rows = read_runs(conn, limit=limit)
        if not rows:
            click.echo("no runs recorded yet")
            return
        for r in rows:
            frac = (
                f"{r['processed']}/{r['total']}"
                if r["total"]
                else str(r["processed"])
            )
            status = r["status"] + (" STALLED" if r["stalled"] else "")
            counters = " ".join(
                f"{k}={v}"
                for k, v in sorted(r["counters"].items())
                if k != "phase"
            )
            phase = r["counters"].get("phase")
            click.echo(
                f"{r['kind']:<9} {r['worker']:<5} {status:<12} {frac:<14}"
                f" {r['rate_per_min']:>7}/min eta {_fmt_eta(r['eta_s']):<8}"
                f" ${r['llm_cost_usd']:.2f} {r['llm_calls']} calls"
                f" {r['errors']} err"
                + (f"  [{phase}]" if phase else "")
            )
            if counters:
                click.echo(f"  {counters}")
            if r["last_error"]:
                click.echo(f"  last error: {r['last_error'][:140]}")
    if not watch:
        _draw()
        return
    while True:
        click.clear()
        _draw()
        _time.sleep(2)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8100, show_default=True)
def serve(host: str, port: int) -> None:
    """Run the dashboard (M7): the JSON API and the client it serves.

    Every filter is still a URL query parameter and every result set still
    comes from SQL — paste a URL in a fresh browser and get the identical view
    back. The client is a Vite build under frontend/; if it has not been built
    yet, every page answers with the command that builds it rather than a
    traceback.
    """
    import uvicorn

    from jobd.web.app import STATIC

    settings = load_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL is unset.")
    if not (STATIC / "index.html").is_file():
        click.echo(
            "The dashboard client is not built. Run:\n"
            "    cd frontend && npm install && npm run build\n"
        )
    click.echo(f"Dashboard: http://{host}:{port}/")
    uvicorn.run("jobd.web.app:app", host=host, port=port)


@main.command()
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket holding raw messages.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Filesystem archive."
)
@click.option("--model", default="default", show_default=True, envvar="JOBD_MODEL")
@click.option("--classify/--no-classify", default=True, show_default=True)
@click.option(
    "--workers",
    default=32,
    show_default=True,
    help="Thread-pool size for the raw fetch (I/O-bound, so real parallelism "
    "even in Python).",
)
@click.confirmation_option(
    prompt="This drops the derived record and rebuilds it from raw. Continue?"
)
def rebuild(
    bucket: str | None,
    local_store: Path | None,
    model: str,
    classify: bool,
    workers: int,
) -> None:
    """Re-derive everything from raw storage (I3).

    Destructive to Postgres and harmless overall, because raw storage is the
    source of truth and the runtime credential cannot delete from it. Gmail is
    not contacted: the bytes are already in the archive.
    """
    from jobd.services.rebuild import rebuild as run_rebuild

    storage = _storage(bucket, local_store)
    provider = _llm(model)

    with _connect() as conn:
        result = run_rebuild(
            storage=storage,
            llm=provider,
            repos=_repos(conn),
            conn=conn,
            classify=classify,
            workers=workers,
        )
        conn.commit()

    click.echo(f"keys seen          {result.keys_seen}")
    click.echo(f"messages restored  {result.messages_restored}")
    for bad in result.unreadable[:10]:
        click.echo(f"  UNREADABLE {bad}", err=True)
    if result.classification:
        click.echo(f"recorded           {result.classification.recorded}")
        click.echo(f"queued             {result.classification.queued_for_review}")


@main.command("import")
@click.option("--bucket", envvar="JOBD_BUCKET", help="S3 bucket holding raw messages.")
@click.option(
    "--local-store", type=click.Path(path_type=Path), help="Filesystem archive."
)
@click.option(
    "--workers",
    default=32,
    show_default=True,
    help="Thread-pool size for the raw fetch (I/O-bound).",
)
@click.option(
    "--batch", default=1000, show_default=True, help="Messages per commit."
)
def import_archive(
    bucket: str | None, local_store: Path | None, workers: int, batch: int
) -> None:
    """Import a raw-message archive into the record — idempotent, resumable.

    For a raw dump some other tool already wrote to storage (in jobd's
    content-hashed envelope layout): reads every key, inserts the message rows
    that are missing, and commits in batches. Safe to re-run — a killed import
    resumes where it left off rather than starting over, and never truncates.
    """
    from jobd.services.rebuild import import_store

    storage = _storage(bucket, local_store)
    with _connect() as conn:
        result = import_store(
            storage=storage,
            repos=_repos(conn),
            conn=conn,
            workers=workers,
            batch=batch,
        )
        conn.commit()
    click.echo(f"keys seen       {result.keys_seen}")
    click.echo(f"inserted        {result.messages_inserted}")
    click.echo(f"already present {result.already_present}")
    for bad in result.unreadable[:10]:
        click.echo(f"  UNREADABLE {bad}", err=True)


if __name__ == "__main__":
    main()
