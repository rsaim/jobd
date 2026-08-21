"""Company logos: fetched once by a CLI run, served from Postgres after that.

WHY THIS IS NOT AN <img src="https://logo.cdn/..."> IN THE DASHBOARD

Pointing the browser at a logo CDN would be four lines of TSX and would tell
that CDN every company the user is talking to, on every page view, forever.
The footer of this app says "local record - nothing leaves this machine", and
a favicon request is still a request. So the network is touched exactly once
per domain, by a command the user runs on purpose (`jobd logos`), and the
bytes are cached in `company_logo` and served from this machine afterwards.
Nothing in the dashboard reaches a third party.

WHAT IT ASKS, AND WHAT IT TELLS THEM

Two proxies, in order, both of which fail honestly when they have nothing:

  1. DuckDuckGo's favicon proxy - the site's own mark, at whatever size the
     site publishes it.
  2. icon.horse - a second opinion for the domains DuckDuckGo has never
     crawled.

Two providers are deliberately NOT in that list. Google's s2/favicons
answers a domain it has never seen with a generic globe, which would fill
the record with identical grey planets that look like data; a provider that
lies is worse than one that has nothing. And the obvious source - the
company's own https://domain/favicon.ico - is the worst option here despite
being the most direct: it would tell every employer in the record the user's
IP address and the moment they opened the page. A proxy is the privacy
choice, not the lazy one.

(Clearbit's logo endpoint was the first provider written here and is gone -
the host no longer resolves. Its absence is why brand marks are favicons.)

Each request carries one thing: a domain the user already corresponds with.
No message content, no names, no identifiers.

A miss is cached too (`source='none'`). Without that, every run would ask the
network about the same few hundred domains that have no logo and never will.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

# A favicon is kilobytes. Anything past this is either a mistake or someone
# else's problem, and it is not going in a row that gets SELECTed per page.
MAX_BYTES = 256 * 1024
TIMEOUT = 6.0

# Some CDNs answer a bare urllib with 403. A plain, honest agent string that
# says what this is beats pretending to be a browser.
_AGENT = "jobd/0.1 (+local personal CRM; one-time logo cache)"

_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("duckduckgo", "https://icons.duckduckgo.com/ip3/{domain}.ico"),
    ("iconhorse", "https://icon.horse/icon/{domain}"),
)


@dataclass(frozen=True, slots=True)
class Fetched:
    company_id: UUID
    domain: str
    image: bytes | None
    content_type: str | None
    source: str


def _get(url: str) -> tuple[bytes, str] | None:
    """One GET, or None. Every failure mode here is ordinary, not exceptional:
    a domain with no logo is a 404, and a dead provider is a timeout."""
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0]
            if not content_type.startswith("image/"):
                return None
            # `read(MAX_BYTES + 1)` rather than read() so a hostile or broken
            # response cannot stream gigabytes into memory.
            body = response.read(MAX_BYTES + 1)
            if not body or len(body) > MAX_BYTES:
                return None
            return body, content_type
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def fetch_one(company_id: UUID, domain: str) -> Fetched:
    """First provider that answers wins; a miss is still an answer."""
    for source, template in _PROVIDERS:
        found = _get(template.format(domain=domain))
        if found:
            image, content_type = found
            return Fetched(company_id, domain, image, content_type, source)
    return Fetched(company_id, domain, None, None, "none")


def pending(
    conn: psycopg.Connection[Any], *, limit: int, refresh: bool = False
) -> list[tuple[UUID, str]]:
    """Companies with a domain and no answer yet.

    `refresh` re-asks about everything, including the misses - the escape
    hatch for "this company got a website since the last run".

    Ordered by how much mail the company accounts for, so a run cut short by
    a rate limit has still covered the companies the user actually looks at.
    """
    where = ["c.domain IS NOT NULL", "length(btrim(c.domain)) > 0"]
    if not refresh:
        # Either never asked, or asked about a domain the company no longer
        # has — a rename leaves the old company's bytes behind otherwise.
        where.append(
            "(l.company_id IS NULL OR l.domain IS DISTINCT FROM lower(c.domain))"
        )
    rows = conn.execute(
        f"""
        SELECT c.id, lower(c.domain), count(m.id) AS weight
        FROM company c
        LEFT JOIN company_logo l ON l.company_id = c.id
        LEFT JOIN message m ON m.company_id = c.id
        WHERE {" AND ".join(where)}
        GROUP BY c.id, c.domain, l.company_id, l.domain
        ORDER BY weight DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def store(conn: psycopg.Connection[Any], results: list[Fetched]) -> None:
    """One statement per batch, not per logo - the same batched-commit shape
    the ingest and classify paths use."""
    if not results:
        return
    conn.cursor().executemany(
        """
        INSERT INTO company_logo (company_id, image, content_type, source, domain,
                                  fetched_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (company_id) DO UPDATE SET
            image = EXCLUDED.image,
            content_type = EXCLUDED.content_type,
            source = EXCLUDED.source,
            domain = EXCLUDED.domain,
            fetched_at = EXCLUDED.fetched_at
        """,
        [
            (r.company_id, r.image, r.content_type, r.source, r.domain)
            for r in results
        ],
    )


def backfill(
    conn: psycopg.Connection[Any],
    *,
    limit: int = 500,
    refresh: bool = False,
    workers: int = 8,
    batch: int = 50,
) -> dict[str, int]:
    """Fetch and cache logos for companies that have a domain.

    Network-bound, so it fans out across threads and commits in batches -
    500 domains one at a time at ~300ms each is three minutes of waiting for
    sockets. Eight is deliberate restraint rather than a tuned number: these
    are somebody else's free endpoints.
    """
    todo = pending(conn, limit=limit, refresh=refresh)
    if not todo:
        return {"asked": 0, "found": 0, "missing": 0}

    found = missing = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending_rows: list[Fetched] = []
        for result in pool.map(lambda item: fetch_one(*item), todo):
            pending_rows.append(result)
            if result.image:
                found += 1
            else:
                missing += 1
            if len(pending_rows) >= batch:
                store(conn, pending_rows)
                conn.commit()
                pending_rows = []
        store(conn, pending_rows)
        conn.commit()

    return {"asked": len(todo), "found": found, "missing": missing}


def logo(conn: psycopg.Connection[Any], company_id: UUID) -> tuple[bytes, str] | None:
    """The cached bytes for one company, or None if there are none."""
    row = conn.execute(
        """
        SELECT image, content_type FROM company_logo
        WHERE company_id = %(id)s AND image IS NOT NULL
        """,
        {"id": company_id},
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return bytes(row[0]), row[1] or "image/png"


def have_logos(conn: psycopg.Connection[Any]) -> list[str]:
    """Every company id that has one.

    The dashboard asks this once and keeps it, rather than requesting a logo
    per company and taking a 404 for most of them: 577 companies is 577
    round trips to learn that 300 of them have nothing.
    """
    rows = conn.execute(
        "SELECT company_id FROM company_logo WHERE image IS NOT NULL"
    ).fetchall()
    return [str(row[0]) for row in rows]


def coverage(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """What a reader needs to know before concluding "this company has no
    logo": how many were asked about at all."""
    row = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM company),
            (SELECT count(*) FROM company
              WHERE domain IS NOT NULL AND length(btrim(domain)) > 0),
            (SELECT count(*) FROM company_logo WHERE image IS NOT NULL),
            (SELECT count(*) FROM company_logo WHERE image IS NULL)
        """
    ).fetchone()
    if row is None:
        return {"companies": 0, "with_domain": 0, "found": 0, "missing": 0}
    return {
        "companies": row[0],
        "with_domain": row[1],
        "found": row[2],
        "missing": row[3],
    }
