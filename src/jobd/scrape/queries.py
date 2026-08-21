"""Gmail query construction — the measured seed pack and its expansions.

Every query here earned its place in the experiment series (docs/
seed-and-expand.md): the seed pack reached 93.5% recall of known job mail on
the reference mailbox, sent-thread pulls alone covered 51%, and the residual
pack contains every positive the rest missed. Numbers in comments are from
that series — change a query, re-run the experiments.

Two Gmail facts shape the code:

* ``resultCountEstimate`` is capped and unreliable; yield is measured by
  paging ``messages.list`` and counting real ids, never by the estimate.
* Category labels (``category:promotions``...) apply per *message*, so
  carving them out of a phrase query cannot hide a whole thread — any
  sibling message a seed catches pulls the thread back in via expansion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobd.domain.prefilter import ATS_DOMAINS, GENERIC_DOMAINS
from jobd.scrape.state import Entity, Query

#: Measured 0.04% positive rate (12 of 29,169) — safe to exclude everywhere.
CARVE = "-category:promotions -category:social -category:forums"

#: LinkedIn routes real recruiter InMail through shared relay addresses while
#: every digest/alert family uses its own noreply sender. from: these two was
#: 71.8% precise on the reference mailbox; from:linkedin.com is mostly noise.
INMAIL_ADDRESSES = ("inmail-hit-reply@linkedin.com", "hit-reply@linkedin.com")

#: Phrases that pulled real positives at usable precision. Each becomes one
#: query so per-family yield is measurable (and an LLM can retire or extend
#: the pack per mailbox later without touching code).
PHRASES: tuple[tuple[str, str], ...] = (
    ("phrase:applying", '"thank you for applying"'),
    ("phrase:application", '"your application"'),
    ("phrase:subj-application", "subject:application"),
    ("phrase:interview", "interview"),
    ("phrase:subj-offer", "subject:offer"),
    ("phrase:recruiter", "recruiter"),
    ("phrase:talent", '"talent acquisition" OR "talent team"'),
    ("phrase:subj-role", "subject:(position OR role)"),
    ("phrase:opportunity", "opportunity"),
)

#: How many ATS domains share one OR-query. Gmail accepts long queries but
#: keeps them readable/debuggable in yield events at this size.
_ATS_CHUNK = 8


def _window(days: int | None) -> str:
    if not days:
        return ""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return f" after:{cutoff:%Y/%m/%d}"


def seed_pack(account: str, window_days: int | None) -> list[Query]:
    """The opening queries for one account, most productive families first."""
    w = _window(window_days)
    queries: list[Query] = [
        # 51% recall on its own once threads are pulled — the user's replies
        # mark the conversations that matter.
        Query(q=f"in:sent{w}", origin="seed:sent", account=account),
        Query(
            q=" OR ".join(f"from:{a}" for a in INMAIL_ADDRESSES) + w,
            origin="seed:inmail",
            account=account,
        ),
    ]
    ats = sorted(ATS_DOMAINS)
    for i in range(0, len(ats), _ATS_CHUNK):
        chunk = ats[i : i + _ATS_CHUNK]
        both = " OR ".join(f"from:{d} OR to:{d}" for d in chunk)
        queries.append(
            Query(q=f"({both}){w}", origin="seed:ats", account=account)
        )
    queries.extend(
        Query(q=f"{q} {CARVE}{w}", origin=f"seed:{name}", account=account)
        for name, q in PHRASES
    )
    return queries


def residual_pack(account: str, window_days: int | None) -> list[Query]:
    """The measured catch-all: every positive the seeds+expansion missed sat
    in mail addressed directly to the user outside the noise categories.
    Listed last so the known-id skip set already removes everything a seed
    or an expansion query claimed first."""
    w = _window(window_days)
    return [
        Query(
            q=f"to:{account} {CARVE}{w}",
            origin="residual:direct",
            account=account,
        )
    ]


def entity_queries(
    entities: list[Entity], account: str, window_days: int | None
) -> list[Query]:
    """Expansion: one query per newly learned domain/address.

    Thread entities are not Gmail queries at all — search cannot filter by
    thread id — so they are resolved by ``threads.get`` in the graph's list
    node instead of appearing here.
    """
    w = _window(window_days)
    out: list[Query] = []
    for e in entities:
        if e.kind == "domain":
            # A learned generic domain (gmail.com...) would pull the whole
            # mailbox; the graph filters these before they get here, this is
            # the belt to that suspender.
            if any(e.value == d or e.value.endswith("." + d) for d in GENERIC_DOMAINS):
                continue
            out.append(
                Query(
                    q=f"(from:{e.value} OR to:{e.value}){w}",
                    origin="expand:domain",
                    account=account,
                )
            )
        elif e.kind == "address":
            out.append(
                Query(
                    q=f"(from:{e.value} OR to:{e.value}){w}",
                    origin="expand:address",
                    account=account,
                )
            )
    return out
