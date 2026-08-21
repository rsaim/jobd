"""Composition root for an automated LinkedIn sync.

`python -m jobd.services.linkedin_sync` (wrapped by `just linkedin-sync`) pulls
the inbox with the cookie-authenticated Voyager client
(:mod:`jobd.adapters.linkedin.voyager_source`) and feeds it through the same
``ingest_raw`` the push endpoint uses. It is the unattended, cron-friendly
alternative to the browser extension and the console collector; the trade-off
it accepts (datacenter-IP Voyager access) is documented on the puller module.

State it keeps, both as plain files so a run is inspectable and resettable:

* ``~/.jobd_linkedin_cookies`` — the pasted session cookie string (input).
* ``~/.jobd/linkedin_watermark`` — ISO timestamp of the newest message ingested
  so far. Absent means a first run, which backfills
  ``JOBD_LINKEDIN_BACKFILL_DAYS`` (default 90). The watermark only advances
  after ingestion succeeds, and ingestion is idempotent, so a crashed run
  re-pulls the same window next time as a server-side no-op rather than losing
  messages.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from jobd.adapters.linkedin.push import account_address, to_raw_message
from jobd.adapters.linkedin.voyager_source import (
    LinkedInAuthError,
    client_from_cookies,
    iter_messages,
    parse_cookie_string,
    self_member_id,
)
from jobd.config import load_settings
from jobd.scrape.service import ScrapeConfigError, _storage
from jobd.services import ingest as ingest_service

COOKIE_FILE = Path.home() / ".jobd_linkedin_cookies"
WATERMARK_FILE = Path.home() / ".jobd" / "linkedin_watermark"


def _load_cookies() -> object:
    if not COOKIE_FILE.exists():
        raise LinkedInAuthError(
            f"{COOKIE_FILE} not found. In a logged-in linkedin.com tab open the "
            "console and run  copy(document.cookie)  then paste into that file "
            f"(chmod 600). It must contain li_at and JSESSIONID."
        )
    return parse_cookie_string(COOKIE_FILE.read_text())


def _read_watermark(default_days: int) -> tuple[datetime, bool]:
    if WATERMARK_FILE.exists():
        text = WATERMARK_FILE.read_text().strip()
        if text:
            return datetime.fromisoformat(text).astimezone(UTC), False
    return datetime.now(UTC) - timedelta(days=default_days), True


def _write_watermark(when: datetime) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK_FILE.write_text(when.astimezone(UTC).isoformat())


def run_sync() -> dict[str, object]:
    """Pull new LinkedIn messages once and ingest them. Returns a summary."""
    settings = load_settings()
    if not settings.database_url:
        raise ScrapeConfigError("DATABASE_URL is unset.")

    backfill_days = int(os.environ.get("JOBD_LINKEDIN_BACKFILL_DAYS", "90"))
    proxy = os.environ.get("JOBD_LINKEDIN_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    jar = _load_cookies()
    api = client_from_cookies(jar, proxies=proxies)
    self_id = self_member_id(api)
    since, first_run = _read_watermark(backfill_days)

    raws = []
    newest = since
    for message in iter_messages(api, self_id=self_id, since=since):
        raws.append(to_raw_message(message, account_slug=self_id))
        if message.sent_at > newest:
            newest = message.sent_at

    from jobd.adapters.postgres import repositories

    storage = _storage(os.environ.get("JOBD_BUCKET"), None)
    with psycopg.connect(settings.database_url) as conn:
        result = ingest_service.ingest_raw(
            raws,
            storage=storage,
            messages=repositories(conn).messages,
            channel="linkedin",
            account=account_address(self_id),
        )
        conn.commit()

    # Advance only after a clean ingest. A message that errored is still in the
    # window next run — the watermark must not step over it.
    if not result.errors and newest > since:
        _write_watermark(newest)

    return {
        "account": self_id,
        "first_run": first_run,
        "since": since.isoformat(),
        "pulled": len(raws),
        "stored": result.stored,
        "already_stored": result.already_stored,
        "rows_inserted": result.rows_inserted,
        "errors": result.errors,
        "watermark": newest.isoformat() if (not result.errors and newest > since) else since.isoformat(),
    }


def main() -> int:
    try:
        summary = run_sync()
    except (LinkedInAuthError, ScrapeConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    label = "backfill" if summary["first_run"] else "delta"
    print(
        f"linkedin {label}: pulled {summary['pulled']}, "
        f"stored {summary['stored']} (already {summary['already_stored']}), "
        f"rows +{summary['rows_inserted']}, account {summary['account']}"
    )
    if summary["errors"]:
        print(f"  {len(summary['errors'])} error(s); watermark held", file=sys.stderr)
        for line in summary["errors"][:10]:
            print(f"    {line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
