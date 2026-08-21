"""Query layer behind the dashboard (M7, PRD P4/P4.1).

Every result set here comes from one SQL statement. M7 gate 2 requires it —
"filters execute in SQL," tested by asserting no application-side filtering of
a fetched row set — and the reason is not purism: M9's `get_communications`
is required to be *the same query* as this module's, not a reimplementation.
A filter that only works because Python looped over rows after the fact would
have to be built twice, and the two would drift.

Whose-turn-is-it (P4.1's "core daily value of a CRM") is the same signal as
`domain.record.is_ghosted` — last message direction, read the other way round:
the last message went *inbound* (from them) and it is the user's turn to
answer; it went *outbound* and it is theirs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg

from jobd.domain import prefilter
from jobd.domain.record import TERMINAL_STAGES, Direction

Turn = Literal["your_turn", "their_turn", "none"]

#: How a message got classified, in the three terms that matter to a reader:
#: free (the deterministic prefilter), free (a learned rule or a thread/rule
#: carry-forward), or paid (a model actually read it). Written once here
#: because two things consume it — the per-row badge and the `/messages`
#: filter — and a badge that disagrees with the filter that produced it is
#: worse than having neither.
#:
#: Keys are the prefixes `mark_classified` writes; the SQL fragment is how the
#: same partition is expressed in a WHERE clause (M7 gate 2 — the filter runs
#: in SQL, it does not re-derive the tag per fetched row).
_TAG_SQL: dict[str, str] = {
    "prefilter": "m.classified_by LIKE 'prefilter:%%'",
    "rule": (
        "(m.classified_by LIKE 'rule:%%' OR m.classified_by LIKE 'thread-carry%%'"
        " OR m.classified_by = 'rule-carry')"
    ),
    "model": (
        "(m.classified_by IS NOT NULL"
        " AND m.classified_by NOT LIKE 'prefilter:%%'"
        " AND m.classified_by NOT LIKE 'rule:%%'"
        " AND m.classified_by NOT LIKE 'thread-carry%%'"
        " AND m.classified_by <> 'rule-carry')"
    ),
    "unclassified": "m.classified_at IS NULL",
}

#: A message the deterministic layer (prefilter or a taught `sender_rule`)
#: resolved to not-job-related. `company_id IS NULL` is the confirming half —
#: `bulk_negative`/`mark_company_negative` set `classified_by` but never a
#: company — so this can never mis-flag a positively-resolved `rule:` row,
#: which always carries one. Deliberately excludes model-classified
#: not-job-related mail (a `verify:` pass, say): "deterministic" means
#: exactly that, prefilter or rule, nothing that cost a model call.
NEGATIVE_SQL = (
    "(m.company_id IS NULL"
    " AND (m.classified_by LIKE 'prefilter:%%' OR m.classified_by LIKE 'rule:%%'))"
)


def classification_tag(classified_by: str | None) -> str:
    """The Python side of `_TAG_SQL`, and the only place the mapping is read.

    `rule:` is the prefix `services.learning.learn_rule` writes when a taught
    rule fans out over the backlog; `thread-carry`/`rule-carry` are
    `classify.py`'s free carry-forward paths. Everything else that has been
    classified cost a model call, including `rulebased` — that provider is an
    extractor, not a shortcut around one.
    """
    if classified_by is None:
        return "unclassified"
    if classified_by.startswith("prefilter:"):
        return "prefilter"
    if (
        classified_by.startswith("rule:")
        or classified_by.startswith("thread-carry")
        or classified_by == "rule-carry"
    ):
        return "rule"
    return "model"


def whose_turn(last_direction: Direction | None) -> Turn:
    """Pure function, not a query — but the same rule everywhere it is asked."""
    if last_direction is None:
        return "none"
    return "your_turn" if last_direction == "inbound" else "their_turn"


@dataclass(frozen=True, slots=True)
class CompanyRow:
    """One row of `/companies`."""

    id: UUID
    canonical_name: str
    domain: str | None
    kind: str
    message_count: int
    application_count: int
    last_touch: datetime | None
    last_direction: Direction | None
    latest_stage: str | None

    @property
    def turn(self) -> Turn:
        return whose_turn(self.last_direction)

    @property
    def status(self) -> str:
        if self.latest_stage in TERMINAL_STAGES:
            return str(self.latest_stage)
        return self.latest_stage or "open"


_SORTS = {
    "recency": "last_touch DESC NULLS LAST",
    "activity": "message_count DESC",
    "name": "c.canonical_name ASC",
}


def _company_filters(
    *,
    search: str | None,
    kind: str | None,
    turn: str | None,
    substantive: bool,
    active_within_days: int | None,
) -> tuple[str, str, dict[str, Any]]:
    """WHERE and HAVING fragments shared by `list_companies` and
    `count_companies`, so "how many are hidden" is counted with exactly the
    predicate that hid them.

    `turn` and `substantive` land in HAVING rather than WHERE because both are
    properties of the aggregate — the last message's direction, and how many
    messages there are at all — not of a single row.
    """
    wheres: list[str] = []
    havings: list[str] = []
    params: dict[str, Any] = {}

    if search:
        wheres.append(
            "(c.canonical_name ILIKE %(search)s OR c.domain ILIKE %(search)s)"
        )
        params["search"] = f"%{search}%"
    if kind in ("employer", "agency"):
        wheres.append("c.kind = %(kind)s")
        params["kind"] = kind
    if substantive:
        # 87 of 189 companies are a single message and 54 are two or three —
        # mostly one-off mentions and false positives. The default view is the
        # search you actually ran; `?all=1` drops this and says how many it
        # was hiding.
        havings.append("(count(DISTINCT m.id) >= 2 OR count(DISTINCT a.id) >= 1)")
    if turn in ("your_turn", "their_turn"):
        wanted = "inbound" if turn == "your_turn" else "outbound"
        havings.append(
            "(array_agg(m.direction ORDER BY m.sent_at DESC)"
            " FILTER (WHERE m.sent_at IS NOT NULL))[1] = %(turn_dir)s"
        )
        params["turn_dir"] = wanted
    if active_within_days:
        havings.append(
            "max(m.sent_at) >= now() - make_interval(days => %(active_days)s)"
        )
        params["active_days"] = active_within_days

    where = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    having = (" HAVING " + " AND ".join(havings)) if havings else ""
    return where, having, params


def list_companies(
    conn: psycopg.Connection[Any],
    *,
    sort: str = "recency",
    search: str | None = None,
    kind: str | None = None,
    turn: str | None = None,
    substantive: bool = False,
    active_within_days: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[CompanyRow]:
    """Every company, message/application counts, last-touch, and its current
    stage — all aggregated in one statement. Companies are never user-created
    (P4.1): this is the whole list, not a filtered view of a bigger one.
    """
    order = _SORTS.get(sort, _SORTS["recency"])
    where, having, params = _company_filters(
        search=search,
        kind=kind,
        turn=turn,
        substantive=substantive,
        active_within_days=active_within_days,
    )
    window = ""
    if limit is not None:
        window = " LIMIT %(limit)s OFFSET %(offset)s"
        params["limit"] = limit
        params["offset"] = offset

    rows = conn.execute(
        f"""
        SELECT
            c.id, c.canonical_name, c.domain, c.kind,
            count(DISTINCT m.id) AS message_count,
            count(DISTINCT a.id) AS application_count,
            max(m.sent_at) AS last_touch,
            (array_agg(m.direction ORDER BY m.sent_at DESC)
                FILTER (WHERE m.sent_at IS NOT NULL))[1] AS last_direction,
            (array_agg(s.stage ORDER BY s.occurred_at DESC)
                FILTER (WHERE s.occurred_at IS NOT NULL))[1] AS latest_stage
        FROM company c
        LEFT JOIN message m ON m.company_id = c.id
        LEFT JOIN application a ON a.company_id = c.id
        LEFT JOIN stage_event s ON s.application_id = a.id
        {where}
        GROUP BY c.id{having}
        ORDER BY {order}{window}
        """,
        params,
    ).fetchall()

    return [
        CompanyRow(
            id=UUID(str(r[0])),
            canonical_name=r[1],
            domain=r[2],
            kind=r[3],
            message_count=int(r[4]),
            application_count=int(r[5]),
            last_touch=r[6],
            last_direction=r[7],
            latest_stage=r[8],
        )
        for r in rows
    ]


def count_companies(
    conn: psycopg.Connection[Any],
    *,
    search: str | None = None,
    kind: str | None = None,
    turn: str | None = None,
    substantive: bool = False,
    active_within_days: int | None = None,
) -> int:
    """How many companies match, ignoring LIMIT. Wraps the same grouped query
    rather than restating the filters, so `/companies` can say "48 shown, 141
    hidden" and have both halves come from one definition."""
    where, having, params = _company_filters(
        search=search,
        kind=kind,
        turn=turn,
        substantive=substantive,
        active_within_days=active_within_days,
    )
    row = conn.execute(
        f"""
        SELECT count(*) FROM (
            SELECT c.id
            FROM company c
            LEFT JOIN message m ON m.company_id = c.id
            LEFT JOIN application a ON a.company_id = c.id
            {where}
            GROUP BY c.id{having}
        ) matched
        """,
        params,
    ).fetchone()
    return 0 if row is None else int(row[0])


def company_turn(conn: psycopg.Connection[Any], company_id: UUID) -> Turn:
    """Whose turn it is with one company.

    `/company/{id}` used to get this by aggregating *every* company and taking
    the first row whose name matched — a full scan to read one field, and one
    that silently picked the wrong company when two shared a name.
    """
    row = conn.execute(
        "SELECT direction FROM message WHERE company_id = %s"
        " ORDER BY sent_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return whose_turn(row[0] if row else None)


@dataclass(frozen=True, slots=True)
class CommunicationRow:
    """One message, as a communications-chain line."""

    id: UUID
    channel: str
    direction: Direction
    sent_at: datetime
    subject: str | None
    contact_name: str | None
    contact_address: str | None
    application_id: UUID | None
    role_title: str | None
    is_stage_evidence: bool
    #: 'employer' | 'agency' | None — which relationship this row represents
    #: *to the company page it's being viewed from*: 'agency' when the row
    #: is reached via the secondary `message_company` link (migration
    #: 0013), 'employer' when reached via the primary `message.company_id`
    #: and that company's own `kind` is 'employer', else None. Absent
    #: entirely when `company_id` wasn't part of the query (e.g. the
    #: unfiltered `/company/{id}` isn't the only caller of this function).
    link_role: str | None = None
    classified_by: str | None = None
    #: Now that `envelope.body_text` cuts a reply at its first quoted-thread
    #: marker (see its docstring), this is cheap enough to carry on every
    #: row instead of needing a separate per-message fetch just to show a
    #: preview in a list.
    body_text: str | None = None

    @property
    def classification_tag(self) -> str:
        """How this message got here — the free/cheap/paid distinction
        `classify.py`'s own progress output already reports in aggregate,
        surfaced per-message on the dashboard. Derived from the same
        `classified_by` strings `mark_classified` writes, not a new column."""
        return classification_tag(self.classified_by)


def list_communications(
    conn: psycopg.Connection[Any],
    *,
    company_id: UUID | None = None,
    application_id: UUID | None = None,
    channel: str | None = None,
    direction: str | None = None,
    contact_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    stage_evidencing: bool | None = None,
    tag: str | None = None,
    has_company: bool | None = None,
    hide_negative: bool = True,
    order: str = "desc",
    limit: int | None = None,
    offset: int = 0,
) -> list[CommunicationRow]:
    """The communications chain, filtered entirely in the WHERE clause.

    Every argument narrows the same query rather than post-filtering a fetched
    list — that is the property M7 gate 2 tests for, and the reason
    `get_communications` (M9) can point at this function unchanged.

    Called with no `company_id` this is also the `/messages` query over the
    whole mailbox, which is why `limit`/`offset` exist: 58k rows is not a page.
    `tag` partitions on how a message was classified (see `_TAG_SQL`), and
    `has_company` separates the recorded 2% from everything the prefilter
    dropped — the only route to a false negative.

    `hide_negative` (default True) drops mail the deterministic layer
    (prefilter or a taught `sender_rule`) resolved to not-job-related —
    `NEGATIVE_SQL` below. A company-scoped call already excludes these
    structurally (they never get a `company_id`), so the flag only changes
    anything for the whole-mailbox view. It stays a flag rather than an
    unconditional filter because `/messages`' own docstring is right that
    this is "the only place a false negative can be found" — the audit path
    is still one click away (`hide_negative=False`), just not the default.
    """
    clauses = ["1=1"]
    # Always bound, even when unset: the SELECT's `link_role` CASE below
    # references %(company_id)s unconditionally (a company_id-scoped view
    # needs to distinguish primary vs. secondary/agency rows even when this
    # function's other callers don't filter on it at all).
    params: dict[str, Any] = {"company_id": company_id}

    if company_id is not None:
        # A message belongs to this company either directly (the primary,
        # timeline-organizing link — `message.company_id`, unchanged from
        # before agencies existed) or secondarily, as the recruiting agency
        # that sourced a message whose primary link is the actual employer
        # (migration 0013's `message_company`). One EXISTS, not a UNION —
        # keeps this a single linear scan, same as every other clause here.
        clauses.append(
            "(m.company_id = %(company_id)s OR EXISTS ("
            "SELECT 1 FROM message_company mc2 WHERE mc2.message_id = m.id"
            " AND mc2.role = 'agency' AND mc2.company_id = %(company_id)s))"
        )
    if application_id is not None:
        clauses.append("m.application_id = %(application_id)s")
        params["application_id"] = application_id
    if channel is not None:
        clauses.append("m.channel = %(channel)s")
        params["channel"] = channel
    if direction is not None:
        clauses.append("m.direction = %(direction)s")
        params["direction"] = direction
    if contact_id is not None:
        clauses.append("m.contact_id = %(contact_id)s")
        params["contact_id"] = contact_id
    if since is not None:
        clauses.append("m.sent_at >= %(since)s")
        params["since"] = since
    if until is not None:
        clauses.append("m.sent_at <= %(until)s")
        params["until"] = until
    if search:
        clauses.append("m.search_tsv @@ websearch_to_tsquery('english', %(search)s)")
        params["search"] = search
    if stage_evidencing is not None:
        exists = "SELECT 1 FROM stage_event se WHERE se.evidence_message_id = m.id"
        clauses.append(
            f"EXISTS ({exists})" if stage_evidencing else f"NOT EXISTS ({exists})"
        )
    if tag in _TAG_SQL:
        clauses.append(_TAG_SQL[tag])
    if hide_negative:
        clauses.append(f"NOT {NEGATIVE_SQL}")
    if has_company is not None:
        clauses.append(
            "m.company_id IS NOT NULL" if has_company else "m.company_id IS NULL"
        )

    direction_sql = "DESC" if order != "asc" else "ASC"
    where = " AND ".join(clauses)
    # Bounded in SQL, not by slicing the result: `/messages` runs this over
    # 58k rows and fetching them all to show 50 would defeat the index the
    # ORDER BY rides on.
    window = ""
    if limit is not None:
        window = " LIMIT %(limit)s OFFSET %(offset)s"
        params["limit"] = limit
        params["offset"] = offset

    rows = conn.execute(
        f"""
        SELECT
            m.id, m.channel, m.direction, m.sent_at, m.subject,
            ct.display_name, ci.identifier,
            m.application_id, a.role_title,
            EXISTS (SELECT 1 FROM stage_event se WHERE se.evidence_message_id = m.id),
            m.classified_by, m.body_text,
            -- Cast, because this parameter is bound even when it is NULL
            -- (the CASE references it unconditionally) and Postgres cannot
            -- infer a bare NULL's type. Without it every company-less caller
            -- — /messages, /contact — fails with AmbiguousParameter.
            CASE
                WHEN %(company_id)s::uuid IS NOT NULL
                     AND mc.company_id = %(company_id)s::uuid
                    THEN 'agency'
                WHEN c.kind IS NOT NULL THEN c.kind
                ELSE NULL
            END AS link_role
        FROM message m
        LEFT JOIN contact ct ON ct.id = m.contact_id
        LEFT JOIN contact_identity ci
            ON ci.contact_id = m.contact_id AND ci.channel = m.channel
        LEFT JOIN application a ON a.id = m.application_id
        LEFT JOIN company c ON c.id = m.company_id
        LEFT JOIN message_company mc ON mc.message_id = m.id AND mc.role = 'agency'
        WHERE {where}
        ORDER BY m.sent_at {direction_sql}{window}
        """,
        params,
    ).fetchall()

    return [
        CommunicationRow(
            id=UUID(str(r[0])),
            channel=r[1],
            direction=r[2],
            sent_at=r[3],
            subject=r[4],
            contact_name=r[5],
            contact_address=r[6],
            application_id=UUID(str(r[7])) if r[7] else None,
            role_title=r[8],
            is_stage_evidence=bool(r[9]),
            classified_by=r[10],
            body_text=r[11],
            link_role=r[12],
        )
        for r in rows
    ]


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text input format (`[1,2,3]`) — not the same as a Postgres
    array literal (`{1,2,3}`), so this can't just hand psycopg a Python list
    and a `::vector` cast. Matches `MessageRepository.set_embedding`'s own
    literal-building, unchanged (the write side of the same column)."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def semantic_search_communications(
    conn: psycopg.Connection[Any],
    query_vector: list[float],
    *,
    company_id: UUID | None = None,
    limit: int = 8,
) -> list[CommunicationRow]:
    """Nearest-neighbor search over `message.embedding` (pgvector cosine
    distance, `<=>`, migration 0020's HNSW index) — semantic, not keyword:
    finds messages *about* the same thing as the query even when they share
    none of its words, which `list_communications`'s `search`
    (`search_tsv`, plain full-text) structurally can't.

    Takes an already-computed vector, not text, so this file stays
    hand-written SQL with no LLM/embedding-provider dependency — the only
    caller, `tools/registry.py`'s `search_communications` (mode="semantic"),
    is where the query text actually gets embedded; it has the chat turn's
    provider on hand, nothing here does.

    Only ever searches messages that *have* an embedding. A message is
    embedded on exactly two paths: `classify --embed` (off by default —
    real cost, doubles model calls) at first classification, or the one-off
    `jobd embed-backfill` catching up whatever predates that. Anything
    neither of those has touched is silently absent from these results —
    the same kind of one-way coverage gap `list_communications`'s
    `hide_negative` documents for keyword search, not a bug in this one.
    """
    clauses = ["m.embedding IS NOT NULL"]
    params: dict[str, Any] = {
        "qvec": _vector_literal(query_vector),
        "limit": limit,
        "company_id": company_id,
    }
    if company_id is not None:
        clauses.append(
            "(m.company_id = %(company_id)s OR EXISTS ("
            "SELECT 1 FROM message_company mc2 WHERE mc2.message_id = m.id"
            " AND mc2.role = 'agency' AND mc2.company_id = %(company_id)s))"
        )
    where = " AND ".join(clauses)

    rows = conn.execute(
        f"""
        SELECT
            m.id, m.channel, m.direction, m.sent_at, m.subject,
            ct.display_name, ci.identifier,
            m.application_id, a.role_title,
            EXISTS (SELECT 1 FROM stage_event se WHERE se.evidence_message_id = m.id),
            m.classified_by, m.body_text,
            CASE
                WHEN %(company_id)s::uuid IS NOT NULL
                     AND mc.company_id = %(company_id)s::uuid
                    THEN 'agency'
                WHEN c.kind IS NOT NULL THEN c.kind
                ELSE NULL
            END AS link_role
        FROM message m
        LEFT JOIN contact ct ON ct.id = m.contact_id
        LEFT JOIN contact_identity ci
            ON ci.contact_id = m.contact_id AND ci.channel = m.channel
        LEFT JOIN application a ON a.id = m.application_id
        LEFT JOIN company c ON c.id = m.company_id
        LEFT JOIN message_company mc ON mc.message_id = m.id AND mc.role = 'agency'
        WHERE {where}
        ORDER BY m.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
        """,
        params,
    ).fetchall()

    return [
        CommunicationRow(
            id=UUID(str(r[0])),
            channel=r[1],
            direction=r[2],
            sent_at=r[3],
            subject=r[4],
            contact_name=r[5],
            contact_address=r[6],
            application_id=UUID(str(r[7])) if r[7] else None,
            role_title=r[8],
            is_stage_evidence=bool(r[9]),
            classified_by=r[10],
            body_text=r[11],
            link_role=r[12],
        )
        for r in rows
    ]


@dataclass(frozen=True, slots=True)
class SemanticHit:
    """One nearest-neighbour result, carrying the two numbers a reader needs
    in order to judge it.

    `distance` is pgvector cosine distance (0 = identical direction, 1 =
    orthogonal, 2 = opposite), surfaced rather than hidden because a ranked
    list with no scale on it invites reading rank 8 as if it meant the same
    thing as rank 1.

    `shared_words` is the query's own content vocabulary that actually
    appears in this message. It is the honest measure of what the embedding
    bought: a hit with an empty tuple is one full-text search could not have
    returned at all, and that is the claim this whole path is making.
    """

    row: CommunicationRow
    company_id: UUID | None
    company_name: str | None
    distance: float
    shared_words: tuple[str, ...]


#: Words carrying no topic. Deliberately small and hand-listed rather than a
#: linguistics dependency: this exists only so `shared_words` doesn't report
#: that a message "shares" the word *the* with the query.
_STOPWORDS = frozenset(
    (
        "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for",
        "with", "from", "about", "into", "over", "after", "before", "is", "are", "was",
        "were", "be", "been", "being", "am", "i", "me", "my", "mine", "we", "us", "our",
        "you", "your", "they", "them", "their", "he", "she", "it", "its", "this",
        "that", "these", "those", "there", "here", "anyone", "someone", "anybody",
        "somebody", "anything", "something", "who", "whom", "what", "when", "where",
        "why", "how", "any", "some", "all", "both", "each", "few", "more", "most",
        "other", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
        "very", "can", "will", "just", "should", "now", "would", "could", "may",
        "might", "must", "have", "has", "had", "do", "does", "did", "done", "get", "got"
    )
)

_WORD_RE = re.compile(r"[a-z][a-z']{2,}")


def _content_words(text: str | None) -> set[str]:
    """Lowercased words of three or more letters, minus the stoplist."""
    if not text:
        return set()
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


#: The one projection all three semantic reads share. `{probe}` is whatever
#: the neighbours are measured against — a literal vector for a typed query,
#: a subquery for "more like this" — and `{where}` narrows the candidates.
#: One string rather than three copies because the day a column is added to
#: a communications line, three drifting copies is how one of them silently
#: keeps returning the old shape.
_SEMANTIC_SELECT = """
    SELECT
        m.id, m.channel, m.direction, m.sent_at, m.subject,
        ct.display_name, ci.identifier,
        m.application_id, a.role_title,
        EXISTS (SELECT 1 FROM stage_event se WHERE se.evidence_message_id = m.id),
        m.classified_by, m.body_text,
        CASE
            WHEN %(company_id)s::uuid IS NOT NULL
                 AND mc.company_id = %(company_id)s::uuid
                THEN 'agency'
            WHEN c.kind IS NOT NULL THEN c.kind
            ELSE NULL
        END AS link_role,
        m.company_id, c.canonical_name,
        m.embedding <=> {probe} AS distance
    FROM message m
    LEFT JOIN contact ct ON ct.id = m.contact_id
    LEFT JOIN contact_identity ci
        ON ci.contact_id = m.contact_id AND ci.channel = m.channel
    LEFT JOIN application a ON a.id = m.application_id
    LEFT JOIN company c ON c.id = m.company_id
    LEFT JOIN message_company mc ON mc.message_id = m.id AND mc.role = 'agency'
    WHERE {where}
    ORDER BY m.embedding <=> {probe}
    LIMIT %(limit)s
"""


def _hits(rows: list[Any], query_words: set[str]) -> list[SemanticHit]:
    return [
        SemanticHit(
            row=CommunicationRow(
                id=UUID(str(r[0])),
                channel=r[1],
                direction=r[2],
                sent_at=r[3],
                subject=r[4],
                contact_name=r[5],
                contact_address=r[6],
                application_id=UUID(str(r[7])) if r[7] else None,
                role_title=r[8],
                is_stage_evidence=bool(r[9]),
                classified_by=r[10],
                body_text=r[11],
                link_role=r[12],
            ),
            company_id=UUID(str(r[13])) if r[13] else None,
            company_name=r[14],
            distance=float(r[15]),
            shared_words=tuple(
                sorted(query_words & (_content_words(r[4]) | _content_words(r[11])))
            ),
        )
        for r in rows
    ]


def semantic_search_ranked(
    conn: psycopg.Connection[Any],
    query_vector: list[float],
    *,
    query_text: str = "",
    company_id: UUID | None = None,
    limit: int = 12,
) -> list[SemanticHit]:
    """`semantic_search_communications` with the score kept.

    Same index, same distance, same coverage caveat — only messages with an
    embedding are candidates, which today means the recorded ones. The
    difference is what comes back: the chat tool wants a list of messages to
    reason over and throws the ranking away, a reader wants to see how close
    each hit actually was and whether it shared any words with what they
    typed.

    Takes a vector, not text, for the same reason its sibling does: this
    module holds hand-written SQL and no embedding-provider dependency. The
    caller embeds. `query_text` is only ever read for word overlap, never
    sent anywhere.
    """
    clauses = ["m.embedding IS NOT NULL"]
    params: dict[str, Any] = {
        "qvec": _vector_literal(query_vector),
        "limit": limit,
        "company_id": company_id,
    }
    if company_id is not None:
        clauses.append(
            "(m.company_id = %(company_id)s OR EXISTS ("
            "SELECT 1 FROM message_company mc2 WHERE mc2.message_id = m.id"
            " AND mc2.role = 'agency' AND mc2.company_id = %(company_id)s))"
        )
    sql = _SEMANTIC_SELECT.format(
        probe="%(qvec)s::vector", where=" AND ".join(clauses)
    )
    rows = conn.execute(sql, params).fetchall()
    return _hits(list(rows), _content_words(query_text))


def semantic_neighbours(
    conn: psycopg.Connection[Any],
    message_id: UUID,
    *,
    limit: int = 12,
) -> list[SemanticHit]:
    """Messages nearest to one you are already reading — "more like this".

    Costs nothing beyond the query: the probe is that message's *stored*
    embedding, so unlike a typed search this path never calls an embedding
    provider. A message that was never embedded has no neighbours to offer
    and returns an empty list rather than an error, because "this message
    predates the backfill" is a coverage fact, not a failure.
    """
    params: dict[str, Any] = {
        "seed": message_id,
        "limit": limit,
        "company_id": None,
    }
    sql = _SEMANTIC_SELECT.format(
        probe="(SELECT embedding FROM message WHERE id = %(seed)s)",
        where=(
            "m.embedding IS NOT NULL AND m.id <> %(seed)s"
            " AND EXISTS (SELECT 1 FROM message WHERE id = %(seed)s"
            " AND embedding IS NOT NULL)"
        ),
    )
    rows = conn.execute(sql, params).fetchall()
    # No query text: nothing was typed, so nothing can be "shared".
    return _hits(list(rows), set())


def embedding_coverage(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """How much of the mailbox semantic search can actually see.

    Shipped with every search result rather than buried in a settings page:
    4% coverage that looks like 100% is the failure mode here — a reader
    concludes the record contains nothing about salary when what happened is
    that the messages about salary were never embedded.
    """
    row = conn.execute("SELECT count(*), count(embedding) FROM message").fetchone()
    if row is None:  # unreachable for an aggregate, but the type allows it
        return {"total": 0, "embedded": 0}
    return {"total": int(row[0]), "embedded": int(row[1])}


@dataclass(frozen=True, slots=True)
class Stats:
    """P4(a): the counting half of G5. Funnel and rates, not inference."""

    total_applications: int
    by_outcome: dict[str, int]
    response_rate: float
    ghost_rate: float
    #: Interview *rounds*, not applications — an application with three
    #: distinct rounds (recruiter_screen, phone_screen, technical, onsite)
    #: counts three times. Distinct on (application_id, stage), not a raw
    #: `stage_event` row count: `stage_event`'s unique index is on
    #: (application_id, stage, evidence_message_id) — one round confirmed by
    #: two emails (an invite, then a "see you tomorrow" reminder) is two
    #: rows for the same round, by design (I2, each claim keeps its own
    #: evidence). Counting rows instead of distinct rounds was a real bug,
    #: not a modeling choice: live data had one application with 30
    #: `phone_screen` rows against a single actual phone screen.
    interviews: int
    #: Estimated hours actually spent *in* interviews so far — a round's
    #: real length is never recorded (nobody's mail says "this call ran 47
    #: minutes"), so this is `interviews` weighted by a per-stage duration
    #: assumption (`_INTERVIEW_HOURS`), not a measurement. Stated as an
    #: estimate everywhere it's shown, not a precise figure.
    interview_hours: float
    #: Estimated hours spent *preparing* — same epistemic status as
    #: `interview_hours`, one level more removed: per-round prep weights
    #: (`_PREP_HOURS`) summed over confirmed rounds, plus a monthly
    #: maintenance baseline (`_PREP_BASELINE_MONTHLY`) for every month that
    #: held at least one round. Nothing records a study session; this is a
    #: round-anchored estimate of the invisible half of the work.
    prep_hours: float
    #: Applications that ever had an offer extended, regardless of how it
    #: ended — reading `by_outcome["offer"]` alone undercounts this, because
    #: an application that went offer -> accepted or offer -> declined is
    #: filed under its *later* outcome, not "offer" (see `_APPLICATION_FACTS`
    #: docstring on why outcome is derived from the latest event only).
    offers: int
    #: `by_outcome["rejected"]`, named explicitly so a template doesn't have
    #: to know the dict key means "the company said no" — as opposed to
    #: `declined` (the candidate said no to an offer) or `withdrawn` (the
    #: candidate left before any decision). Three different endings; this is
    #: the one that is actually a rejection.
    rejections: int
    #: The conversion funnel, stage by stage. `engaged` is applications where
    #: a real conversation existed (you wrote to them, or the process reached
    #: a stage no single email can mint); `replied` is the numerator of
    #: `response_rate` (an inbound message AFTER your first outbound one);
    #: `interviewed` is applications with at least one confirmed round —
    #: apps, not rounds, so it chains with the other stages.
    engaged: int
    replied: int
    interviewed: int
    #: Momentum: confirmed rounds and outbound messages in the last 30 days
    #: vs the 30 days before that — the "is the search heating or cooling"
    #: reading the all-time totals can't give.
    rounds_30d: int
    rounds_prev_30d: int
    outbound_30d: int
    outbound_prev_30d: int
    #: Median days from your first outbound message to the company's first
    #: reply after it, over applications that ever replied. None when nothing
    #: has ever replied.
    reply_lag_days: float | None
    #: Whose move is it, over live (non-terminal) engaged applications:
    #: `court_yours` — their mail is the latest, you owe the reply;
    #: `court_theirs` — your mail is the latest, the wait is on them.
    court_yours: int
    court_theirs: int


#: Stage events that say how an application ended (or that an offer landed),
#: newest-first per application, used to fill in `application.outcome` when
#: nothing ever wrote it. Note `offer` is here but is NOT in
#: `record.TERMINAL_STAGES` — an offer is an outcome worth counting and not an
#: ending, and conflating the two would stop `is_ghosted` firing on an offer
#: that went silent.
_OUTCOME_STAGES = ("offer", "accepted", "rejected", "withdrawn", "declined")

#: Interview-round stages, for the "how many interviews" count — deliberately
#: excludes "applied" (not an interview) and every terminal stage (an ending,
#: not a round).
_INTERVIEW_STAGES = ("recruiter_screen", "phone_screen", "technical", "onsite")

#: Stages that cannot be minted out of a single unanswered email — reaching
#: any of them means a real conversation happened, whatever the mail record
#: shows. Used to decide whether an application was ever *engaged* (below).
_BEYOND_SCREEN_STAGES = ("phone_screen", "technical", "onsite", "offer", "accepted")

#: One row per *confirmed* interview round: distinct (application_id, stage)
#: with the earliest evidence date as the round's day. `recruiter_screen`
#: needs corroboration — an outbound message to the same company near the
#: round's date — because the extractor mints that stage from the first
#: recruiter email, so an unanswered cold pitch carries the stage without any
#: call having happened. Live numbers behind the gate: 441 recruiter_screen
#: rounds in the reference record, only 78 with outbound mail within the
#: window — the other 363 were inbox traffic, not calls. The later stages
#: skip the check; nothing mints a technical or an onsite out of one inbound
#: message. Window is asymmetric (-7d/+14d) because you usually reply first
#: and the call lands within the following two weeks.
_CONFIRMED_ROUNDS = """
    SELECT r.application_id, r.stage, r.d
    FROM (
        SELECT se.application_id, se.stage, min(se.occurred_at) AS d
        FROM stage_event se
        WHERE se.stage = ANY(%(stages)s)
        GROUP BY se.application_id, se.stage
    ) r
    JOIN application a ON a.id = r.application_id
    WHERE r.stage <> 'recruiter_screen'
       OR EXISTS (
            SELECT 1 FROM message m
            WHERE m.company_id = a.company_id
              AND m.direction = 'outbound'
              AND m.sent_at BETWEEN r.d - interval '7 days'
                                AND r.d + interval '14 days')
"""

#: Assumed length of one round, in hours — a real-world default per stage,
#: not derived from any recorded data (nothing captures how long a call
#: actually ran). A recruiter screen and a phone screen both land near 30
#: minutes; "technical" covers everything from a single hour-long round to a
#: pairing exercise, so it takes the middle of that range; "onsite" is a
#: same-day loop, several rounds back to back, not one hour-long meeting.
_INTERVIEW_HOURS = {
    "recruiter_screen": 0.5,
    "phone_screen": 0.5,
    "technical": 1.0,
    "onsite": 3.0,
}

#: Assumed prep behind one round, in hours — same status as
#: `_INTERVIEW_HOURS`: a defensible default, not a measurement (nothing in
#: a mailbox records a leetcode session). What each weight stands for:
#: a recruiter screen costs company-and-role research; a phone screen adds
#: warm-up problems and a story refresh; a technical round is preceded by
#: targeted DS/algo practice; an onsite is the big one — system design
#: prep plus a fundamentals sweep (OS, DBMS, networking, AI fundamentals
#: for AI roles) and usually a mock loop.
_PREP_HOURS = {
    "recruiter_screen": 0.5,
    "phone_screen": 2.0,
    "technical": 4.0,
    "onsite": 8.0,
}

#: The standing grind while the search is live, per month that had at least
#: one confirmed round: roughly two hours a week of maintenance practice
#: (leetcode cadence, reading) that no single round can claim.
_PREP_BASELINE_MONTHLY = 8.0

#: One row per application with everything the funnel needs: the recorded
#: outcome, the outcome implied by its stage events, the latest stage of any
#: kind, and the last-message facts `is_ghosted` reads.
_APPLICATION_FACTS = """
    WITH outcome_event AS (
        SELECT DISTINCT ON (application_id) application_id, stage
        FROM stage_event
        WHERE stage = ANY(%(outcome_stages)s)
        ORDER BY application_id, occurred_at DESC
    ),
    latest_event AS (
        SELECT DISTINCT ON (application_id) application_id, stage
        FROM stage_event
        ORDER BY application_id, occurred_at DESC
    ),
    -- An application that went offer -> accepted or offer -> declined files
    -- under its later outcome above (outcome_event is latest-only), so "had
    -- an offer at all" needs its own flag rather than reading `outcome`.
    had_offer AS (
        SELECT DISTINCT application_id FROM stage_event WHERE stage = 'offer'
    ),
    -- Interview evidence, for the outcome-inferred offer path below. The
    -- self-evidencing outcome stages (accepted/declined) don't count here —
    -- they're exactly what's being validated.
    had_interview AS (
        SELECT DISTINCT application_id FROM stage_event
        WHERE stage IN ('recruiter_screen', 'phone_screen', 'technical', 'onsite')
    )
    SELECT a.id,
           coalesce(a.outcome, oe.stage) AS outcome,
           le.stage AS latest_stage,
           max(m.sent_at) FILTER (WHERE m.direction = 'outbound') AS last_out,
           (array_agg(m.direction ORDER BY m.sent_at DESC)
               FILTER (WHERE m.sent_at IS NOT NULL))[1] AS last_dir,
           -- An offer either left a real offer event, or is inferred from an
           -- accepted/declined outcome — but inference needs corroboration:
           -- a direct employer (an agency's accepted/declined is about its
           -- *pitch*, not an offer) plus evidence a process actually ran
           -- (an interview stage, or the person writing to them). Without
           -- that gate, "thanks, I'll pass" on a cold pitch counted as a
           -- declined offer — live-caught inflating offers 19 vs ~10 real.
           (ho.application_id IS NOT NULL
               OR (coalesce(a.outcome, oe.stage) IN ('accepted', 'declined')
                   AND coalesce(c.kind, 'employer') <> 'agency'
                   AND (hi.application_id IS NOT NULL
                        OR bool_or(m.direction = 'outbound')))
           ) AS had_offer
    FROM application a
    LEFT JOIN company c ON c.id = a.company_id
    LEFT JOIN outcome_event oe ON oe.application_id = a.id
    LEFT JOIN latest_event le ON le.application_id = a.id
    LEFT JOIN had_offer ho ON ho.application_id = a.id
    LEFT JOIN had_interview hi ON hi.application_id = a.id
    LEFT JOIN message m ON m.application_id = a.id
    GROUP BY a.id, c.kind, oe.stage, le.stage, ho.application_id,
             hi.application_id
"""


def compute_stats(
    conn: psycopg.Connection[Any], *, now: datetime | None = None
) -> Stats:
    """Funnel counts and rates, aggregated in SQL. The relationship-intelligence
    and market-signal halves of G5 are post-v1 (build guide, M7) — this ships
    the counting only.

    **Outcome is derived, not just read.** `application.outcome` is written by
    the extractor and frequently is not: 9 applications hold an `offer` stage
    event and 2 a `rejected` one while the column is still NULL, so reading
    the column alone reported zero offers against 43 recorded offer events.
    `coalesce(outcome, <latest outcome-bearing stage event>)` closes that,
    and it stays derived rather than back-filled so a better extractor
    re-derives the world for free (I3).

    The same query fixes a second bug in the ghost rate: `is_ghosted` was being
    handed `a.outcome` as its `latest_stage`, so an application whose stage
    events reached `rejected` but whose column was NULL looked non-terminal and
    could be counted as ghosted. It now gets the actual latest stage.
    """
    at = now or datetime.now(UTC)

    rows = conn.execute(
        _APPLICATION_FACTS, {"outcome_stages": list(_OUTCOME_STAGES)}
    ).fetchall()
    total_n = len(rows)

    # Ghosting and the court split both need an engaged application: you
    # wrote to them, or the process reached a stage no single email can
    # mint. A cold pitch that was never answered didn't ghost anyone and
    # nobody owes anyone a reply on it — it was never a conversation.
    engaged_rows = conn.execute(
        """
        SELECT a.id FROM application a
        WHERE EXISTS (SELECT 1 FROM message m
                      WHERE m.company_id = a.company_id
                        AND m.direction = 'outbound')
           OR EXISTS (SELECT 1 FROM stage_event se
                      WHERE se.application_id = a.id
                        AND se.stage = ANY(%(beyond)s))
        """,
        {"beyond": list(_BEYOND_SCREEN_STAGES)},
    ).fetchall()
    engaged = {row[0] for row in engaged_rows}

    by_outcome: dict[str, int] = {}
    from jobd.domain.record import TERMINAL_STAGES, is_ghosted

    ghosted_ids: list[Any] = []
    offers_n = 0
    court_yours = court_theirs = 0
    for _id, outcome, latest_stage, last_out, last_dir, had_offer in rows:
        key = str(outcome) if outcome else "open"
        by_outcome[key] = by_outcome.get(key, 0) + 1
        if had_offer:
            offers_n += 1
        ghosted = is_ghosted(
            last_message_at=last_out,
            last_direction=last_dir,
            latest_stage=latest_stage,
            now=at,
        )
        if ghosted:
            ghosted_ids.append(_id)
        terminal = (
            outcome in TERMINAL_STAGES or latest_stage in TERMINAL_STAGES
        )
        # A ghosted application is not "waiting on them" — the wait already
        # curdled into an ending, and it has its own count.
        if not terminal and not ghosted and _id in engaged:
            if last_dir == "inbound":
                court_yours += 1
            elif last_dir == "outbound":
                court_theirs += 1

    # Responded: an inbound message arrived AFTER your first outbound one —
    # a company actually answering you. The old definition ("any inbound
    # message ever landed on the application") reported 93.6% on the
    # reference record, because most applications here are *seeded by* an
    # inbound recruiter pitch: the mail that created the application also
    # counted as the company responding to it. Denominator is applications
    # you ever wrote to, not all applications — an unanswered cold pitch you
    # ignored is not a non-response, it's a non-conversation.
    mail = conn.execute(
        """
        WITH app_mail AS (
            SELECT a.id,
                   min(m.sent_at) FILTER (WHERE m.direction = 'outbound')
                       AS first_out,
                   max(m.sent_at) FILTER (WHERE m.direction = 'inbound')
                       AS last_in
            FROM application a
            JOIN message m ON m.company_id = a.company_id
            GROUP BY a.id
        )
        SELECT count(*) FILTER (WHERE first_out IS NOT NULL),
               count(*) FILTER (WHERE first_out IS NOT NULL
                                  AND last_in > first_out)
        FROM app_mail
        """
    ).fetchone()
    reached_out_n, replied_n = (int(mail[0]), int(mail[1])) if mail else (0, 0)

    # Confirmed rounds only (`_CONFIRMED_ROUNDS`) — a recruiter_screen row
    # minted from an unanswered pitch is not an interview, and it was 363 of
    # the 582 "interviews" this used to report.
    interviews = conn.execute(
        f"SELECT count(*) FROM ({_CONFIRMED_ROUNDS}) r",
        {"stages": list(_INTERVIEW_STAGES)},
    ).fetchone()
    interviews_n = int(interviews[0]) if interviews else 0

    by_stage = conn.execute(
        f"SELECT stage, count(*) FROM ({_CONFIRMED_ROUNDS}) r GROUP BY stage",
        {"stages": list(_INTERVIEW_STAGES)},
    ).fetchall()
    hours = sum(_INTERVIEW_HOURS[stage] * n for stage, n in by_stage)

    # Prep: the same confirmed rounds through the prep weights, plus the
    # monthly baseline for every month that held at least one round.
    active_months = conn.execute(
        f"""
        SELECT count(DISTINCT date_trunc('month', r.d))
        FROM ({_CONFIRMED_ROUNDS}) r
        """,
        {"stages": list(_INTERVIEW_STAGES)},
    ).fetchone()
    months_n = int(active_months[0]) if active_months else 0
    prep = months_n * _PREP_BASELINE_MONTHLY + sum(
        _PREP_HOURS[stage] * n for stage, n in by_stage
    )

    # Applications with at least one confirmed round — the funnel's
    # "interviewed" stage. Distinct apps, where `interviews_n` above counts
    # rounds; both read the same gated subquery.
    interviewed = conn.execute(
        f"SELECT count(DISTINCT r.application_id) FROM ({_CONFIRMED_ROUNDS}) r",
        {"stages": list(_INTERVIEW_STAGES)},
    ).fetchone()
    interviewed_n = int(interviewed[0]) if interviewed else 0

    # Momentum: the same confirmed rounds, windowed. 30 days is one hiring
    # cycle — long enough to smooth a quiet week, short enough to move.
    momentum = conn.execute(
        f"""
        SELECT count(*) FILTER (WHERE r.d >= %(a30)s),
               count(*) FILTER (WHERE r.d >= %(a60)s AND r.d < %(a30)s)
        FROM ({_CONFIRMED_ROUNDS}) r
        """,
        {
            "stages": list(_INTERVIEW_STAGES),
            "a30": at - timedelta(days=30),
            "a60": at - timedelta(days=60),
        },
    ).fetchone()
    rounds_30d, rounds_prev = (int(momentum[0]), int(momentum[1])) if momentum else (0, 0)

    out_momentum = conn.execute(
        """
        SELECT count(*) FILTER (WHERE sent_at >= %(a30)s),
               count(*) FILTER (WHERE sent_at >= %(a60)s AND sent_at < %(a30)s)
        FROM message
        WHERE direction = 'outbound' AND company_id IS NOT NULL
        """,
        {"a30": at - timedelta(days=30), "a60": at - timedelta(days=60)},
    ).fetchone()
    out_30d, out_prev = (int(out_momentum[0]), int(out_momentum[1])) if out_momentum else (0, 0)

    # Median wait for a first reply: your first outbound message to the
    # company's first inbound one after it, per application, median across
    # every application that ever replied. The number that calibrates "is
    # this silence normal yet".
    lag = conn.execute(
        """
        WITH firsts AS (
            SELECT a.id, a.company_id,
                   min(m.sent_at) FILTER (WHERE m.direction = 'outbound')
                       AS first_out
            FROM application a
            JOIN message m ON m.company_id = a.company_id
            GROUP BY a.id, a.company_id
        ),
        waits AS (
            SELECT f.id,
                   extract(epoch FROM min(m.sent_at) - f.first_out) / 86400.0
                       AS days
            FROM firsts f
            JOIN message m ON m.company_id = f.company_id
             AND m.direction = 'inbound' AND m.sent_at > f.first_out
            WHERE f.first_out IS NOT NULL
            GROUP BY f.id, f.first_out
        )
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY days) FROM waits
        """
    ).fetchone()
    reply_lag = float(lag[0]) if lag and lag[0] is not None else None

    ghosted_engaged_n = sum(1 for app_id in ghosted_ids if app_id in engaged)

    return Stats(
        total_applications=total_n,
        by_outcome=by_outcome,
        response_rate=replied_n / reached_out_n if reached_out_n else 0.0,
        ghost_rate=ghosted_engaged_n / len(engaged) if engaged else 0.0,
        interviews=interviews_n,
        interview_hours=hours,
        prep_hours=prep,
        offers=offers_n,
        rejections=by_outcome.get("rejected", 0),
        engaged=len(engaged),
        replied=replied_n,
        interviewed=interviewed_n,
        rounds_30d=rounds_30d,
        rounds_prev_30d=rounds_prev,
        outbound_30d=out_30d,
        outbound_prev_30d=out_prev,
        reply_lag_days=reply_lag,
        court_yours=court_yours,
        court_theirs=court_theirs,
    )


@dataclass(frozen=True, slots=True)
class HomeStats:
    """Aggregate counts for the '/' dashboard — the classify pipeline's own
    funnel (unclassified → filtered/recorded/queued), not `/companies`'
    Stats above (which is the job-search funnel: applications/response/
    ghost rate). Two different funnels, two different questions."""

    total_messages: int
    unclassified: int
    negative_filtered: int
    recorded: int
    not_job_related: int
    queued_for_review: int
    companies_by_kind: dict[str, int]
    rules_by_source: dict[str, int]
    review_queue_pending: int


def compute_home_stats(conn: psycopg.Connection[Any]) -> HomeStats:
    """The pipeline's own view of itself — how much of the mailbox has been
    swept, and by which mechanism. Hand-written SQL like the rest of this
    module (see the module docstring on why reads live here, not in
    `adapters/postgres/repositories.py`)."""
    total = conn.execute("SELECT count(*) FROM message").fetchone()
    funnel = conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE classified_at IS NULL) AS unclassified,
            count(*) FILTER (WHERE classified_by = 'prefilter:negative') AS negative,
            count(*) FILTER (
                WHERE classified_at IS NOT NULL AND company_id IS NOT NULL
            ) AS recorded,
            count(*) FILTER (
                WHERE classified_at IS NOT NULL
                  AND company_id IS NULL
                  AND classified_by <> 'prefilter:negative'
                  AND NOT EXISTS (
                      SELECT 1 FROM review_queue rq
                      WHERE rq.message_id = message.id AND rq.status = 'pending'
                  )
            ) AS not_job_related,
            count(*) FILTER (
                WHERE EXISTS (
                    SELECT 1 FROM review_queue rq
                    WHERE rq.message_id = message.id AND rq.status = 'pending'
                )
            ) AS queued
        FROM message
        """
    ).fetchone()
    assert funnel is not None
    kinds = conn.execute("SELECT kind, count(*) FROM company GROUP BY kind").fetchall()
    sources = conn.execute(
        "SELECT source, count(*) FROM sender_rule GROUP BY source"
    ).fetchall()
    pending = conn.execute(
        "SELECT count(*) FROM review_queue WHERE status = 'pending'"
    ).fetchone()
    return HomeStats(
        total_messages=int(total[0]) if total else 0,
        unclassified=int(funnel[0]),
        negative_filtered=int(funnel[1]),
        recorded=int(funnel[2]),
        not_job_related=int(funnel[3]),
        queued_for_review=int(funnel[4]),
        companies_by_kind={str(k): int(n) for k, n in kinds},
        rules_by_source={str(k): int(n) for k, n in sources},
        review_queue_pending=int(pending[0]) if pending else 0,
    )


@dataclass(frozen=True, slots=True)
class VerificationRow:
    """A company page's most recent audit pass (`services/verify.py`,
    migration 0015) — a finding to display, not something the dashboard
    itself acts on."""

    id: UUID
    model: str
    message_count: int
    classification_correct: bool | None
    verified_name: str | None
    verified_kind: str | None
    verified_domain: str | None
    reasoning: str | None
    suggested_rules: list[dict[str, Any]]
    created_at: datetime


def latest_verification(
    conn: psycopg.Connection[Any], company_id: UUID
) -> VerificationRow | None:
    """The company page's audit panel — one query, most recent pass only.
    `None` means this company has never been through `jobd verify`."""
    row = conn.execute(
        "SELECT id, model, message_count, classification_correct, verified_name,"
        " verified_kind, verified_domain, reasoning, suggested_rules, created_at"
        " FROM company_verification WHERE company_id = %s"
        " ORDER BY created_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    if row is None:
        return None
    return VerificationRow(
        id=UUID(str(row[0])),
        model=row[1],
        message_count=int(row[2]),
        classification_correct=row[3],
        verified_name=row[4],
        verified_kind=row[5],
        verified_domain=row[6],
        reasoning=row[7],
        suggested_rules=row[8] or [],
        created_at=row[9],
    )


# --------------------------------------------------------------- activity


#: Daily-count buckets for the activity graph, read off the real
#: distribution rather than picked round: 83 days sit at exactly 1 message
#: and 36 at 2, the mean is 5, and the busiest day is 28. Even buckets would
#: put almost every active day in the first shade and waste the ramp.
_ACTIVITY_LEVELS = (1, 3, 6, 11)


def _level(count: int) -> int:
    """0 for a silent day, then 1..4 as the count crosses each bucket."""
    if count <= 0:
        return 0
    return sum(1 for threshold in _ACTIVITY_LEVELS if count >= threshold)


@dataclass(frozen=True, slots=True)
class ActivityDay:
    """One cell of the activity graph."""

    day: date
    count: int

    @property
    def level(self) -> int:
        return _level(self.count)


@dataclass(frozen=True, slots=True)
class ActivityCalendar:
    """The activity graph, already shaped as the grid it renders as.

    Built here rather than in Jinja because "lay 18 months of dates out as
    week-columns of weekday-rows, with the leading and trailing partial weeks
    padded" is arithmetic, and a template that does arithmetic is a template
    nobody can change safely. `weeks[i][j]` is week i, weekday j (Sun..Sat),
    or None for a cell outside the range.
    """

    weeks: list[list[ActivityDay | None]]
    #: (week index, short month name) for the labels above the grid, one per
    #: month, positioned at the first week that month appears in.
    month_labels: list[tuple[int, str]]
    total: int
    active_days: int
    busiest: ActivityDay | None
    since: date
    until: date
    #: Which series the cells count: "interviews" (confirmed rounds) or
    #: "messages" (inbound job-linked mail). The client labels cells with it.
    metric: str = "interviews"


def daily_activity(
    conn: psycopg.Connection[Any],
    *,
    company_id: UUID | None = None,
    since: date,
    until: date,
    metric: str = "interviews",
) -> dict[date, int]:
    """Interview *rounds* per day, in one grouped query — the activity graph
    reads as "which days had an interview happen", not "which days had any
    job-related mail". A message landing in the inbox isn't activity in the
    sense this graph is for; a recruiter screen or an onsite is.

    Distinct (application_id, stage), by the *earliest* `occurred_at` among
    that round's evidence rows — not a raw `stage_event` count. A round
    confirmed by two emails (an invite, then a same-day reminder) is two
    rows in `stage_event` by design (its unique index is on
    (application_id, stage, evidence_message_id), not (application_id,
    stage) — see `Stats.interviews`' docstring), so counting rows instead
    of distinct rounds inflated this graph — one real application showed 30
    `phone_screen` "events" for a single phone screen. The earliest
    evidence date is used as the round's canonical day, since that's
    normally closest to when it actually happened (a later confirmation
    email restates it, doesn't move it).
    """
    params: dict[str, Any] = {
        "since": since,
        "until": until + timedelta(days=1),
    }

    if metric == "messages":
        # Inbound job-related mail per day — the traffic view the graph used
        # to be mistaken for, now available on purpose as its own metric.
        # Company-linked messages only: unlinked mail is by definition not
        # (yet) part of the record this dashboard reports on.
        company_clause = ""
        if company_id is not None:
            company_clause = " AND m.company_id = %(company_id)s"
            params["company_id"] = company_id
        rows = conn.execute(
            f"""
            SELECT m.sent_at::date, count(*)
            FROM message m
            WHERE m.direction = 'inbound' AND m.company_id IS NOT NULL
              {company_clause}
              AND m.sent_at >= %(since)s AND m.sent_at < %(until)s
            GROUP BY 1
            """,
            params,
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    params["stages"] = list(_INTERVIEW_STAGES)
    company_clause = ""
    if company_id is not None:
        company_clause = " AND a.company_id = %(company_id)s"
        params["company_id"] = company_id
    rows = conn.execute(
        f"""
        SELECT round.d::date, count(*)
        FROM ({_CONFIRMED_ROUNDS}) round
        JOIN application a ON a.id = round.application_id
        WHERE round.d >= %(since)s AND round.d < %(until)s{company_clause}
        GROUP BY 1
        """,
        params,
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def activity_calendar(
    conn: psycopg.Connection[Any],
    *,
    company_id: UUID | None = None,
    days: int = 371,
    today: date | None = None,
    metric: str = "interviews",
) -> ActivityCalendar:
    """`daily_activity`, laid out as the week-column grid the graph renders.

    371 days = 53 whole weeks, so the grid is always the same width and the
    left edge lands on a week boundary instead of drifting by weekday.
    """
    end = today or datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    counts = daily_activity(
        conn, company_id=company_id, since=start, until=end, metric=metric
    )

    # Sunday-first columns: back up to the Sunday on or before `start`.
    # weekday() is Mon=0..Sun=6, so Sunday is 6 and everything else is +1.
    grid_start = start - timedelta(days=(start.weekday() + 1) % 7)

    weeks: list[list[ActivityDay | None]] = []
    month_labels: list[tuple[int, str]] = []
    seen_months: set[tuple[int, int]] = set()
    cursor = grid_start
    while cursor <= end:
        column: list[ActivityDay | None] = []
        for _ in range(7):
            if cursor < start or cursor > end:
                column.append(None)
            else:
                column.append(ActivityDay(day=cursor, count=counts.get(cursor, 0)))
                key = (cursor.year, cursor.month)
                if key not in seen_months:
                    seen_months.add(key)
                    month_labels.append((len(weeks), cursor.strftime("%b")))
            cursor += timedelta(days=1)
        weeks.append(column)

    busiest = max(
        (ActivityDay(day=d, count=n) for d, n in counts.items()),
        key=lambda a: a.count,
        default=None,
    )
    return ActivityCalendar(
        weeks=weeks,
        month_labels=month_labels,
        total=sum(counts.values()),
        active_days=len(counts),
        busiest=busiest,
        since=start,
        until=end,
        metric=metric,
    )


# ----------------------------------------------------------------- people


@dataclass(frozen=True, slots=True)
class PersonRow:
    """One correspondent on a company page."""

    contact_id: UUID
    display_name: str | None
    address: str
    channel: str
    role_title: str | None
    message_count: int
    first_at: datetime | None
    last_at: datetime | None
    #: Distinct stages evidenced by this person's messages — what they are on
    #: the record for having done, rather than merely that they wrote to you.
    stages: tuple[str, ...]
    #: How many companies this contact is linked to in total. >1 is the
    #: recruiter-across-several-firms case `contact_company` is time-bounded
    #: for.
    company_count: int
    #: Why this is a system rather than a person, or None. From
    #: `prefilter.is_automated`, so the ATS list has one definition.
    automated_reason: str | None

    @property
    def name(self) -> str:
        """Never blank: 40 of 155 contacts have no display name at all, and
        '(unnamed)' tells the reader less than the local part does."""
        if self.display_name:
            return self.display_name
        return self.address.partition("@")[0] or self.address

    @property
    def is_system(self) -> bool:
        return self.automated_reason is not None


def people_for_company(
    conn: psycopg.Connection[Any],
    company_id: UUID,
    *,
    self_address: str | None = None,
) -> list[PersonRow]:
    """Everyone who corresponded with this company, enriched, in one query.

    Two corrections the raw `contact_company` join needs before it is worth
    showing. Self-exclusion happens in SQL: the user's own address is a
    contact linked to 73 companies and 12k messages, so without it the panel
    leads with the reader on every page. System-vs-person is decided in Python
    by `prefilter.is_automated` — reusing the classifier's own ATS list rather
    than restating a suffix-match rule in SQL where the two could drift. It is
    a display grouping, not a filter, so no row is dropped by it.
    """
    params: dict[str, Any] = {"company_id": company_id}
    self_clause = ""
    if self_address:
        self_clause = " AND lower(ci.identifier) <> lower(%(self_address)s)"
        params["self_address"] = self_address

    rows = conn.execute(
        f"""
        SELECT ct.id, ct.display_name, ci.identifier, ci.channel, cc.role_title,
               count(DISTINCT m.id) AS message_count,
               min(m.sent_at) AS first_at,
               max(m.sent_at) AS last_at,
               array_remove(array_agg(DISTINCT se.stage), NULL) AS stages,
               (SELECT count(*) FROM contact_company cc2
                 WHERE cc2.contact_id = ct.id) AS company_count
        FROM contact ct
        JOIN contact_company cc
          ON cc.contact_id = ct.id AND cc.company_id = %(company_id)s
        JOIN contact_identity ci ON ci.contact_id = ct.id{self_clause}
        LEFT JOIN message m
          ON m.contact_id = ct.id AND m.company_id = %(company_id)s
        LEFT JOIN stage_event se ON se.evidence_message_id = m.id
        GROUP BY ct.id, ct.display_name, ci.identifier, ci.channel, cc.role_title
        ORDER BY message_count DESC, ct.display_name NULLS LAST
        """,
        params,
    ).fetchall()

    return [
        PersonRow(
            contact_id=UUID(str(r[0])),
            display_name=r[1],
            address=r[2],
            channel=r[3],
            role_title=r[4],
            message_count=int(r[5]),
            first_at=r[6],
            last_at=r[7],
            stages=tuple(r[8] or ()),
            company_count=int(r[9]),
            automated_reason=prefilter.is_automated(r[2]),
        )
        for r in rows
    ]


@dataclass(frozen=True, slots=True)
class ContactCompanyRow:
    """One company a contact is linked to, for `/contact/{id}`."""

    company_id: UUID
    canonical_name: str
    kind: str
    role_title: str | None
    message_count: int
    first_seen_at: datetime
    last_seen_at: datetime


def contact_detail(
    conn: psycopg.Connection[Any], contact_id: UUID
) -> tuple[str | None, list[tuple[str, str]], list[ContactCompanyRow]] | None:
    """One person across every company they touched.

    The payoff for `contact_company` being time-bounded rather than a column:
    a recruiter who mailed you from three firms keeps all three relationships,
    and this is the only surface that shows them side by side.
    """
    row = conn.execute(
        "SELECT display_name FROM contact WHERE id = %s", (contact_id,)
    ).fetchone()
    if row is None:
        return None
    identities = conn.execute(
        "SELECT channel, identifier FROM contact_identity WHERE contact_id = %s"
        " ORDER BY channel, identifier",
        (contact_id,),
    ).fetchall()
    companies = conn.execute(
        """
        SELECT c.id, c.canonical_name, c.kind, cc.role_title,
               count(m.id) AS message_count,
               cc.first_seen_at, cc.last_seen_at
        FROM contact_company cc
        JOIN company c ON c.id = cc.company_id
        LEFT JOIN message m ON m.contact_id = cc.contact_id AND m.company_id = c.id
        WHERE cc.contact_id = %s
        GROUP BY c.id, c.canonical_name, c.kind, cc.role_title,
                 cc.first_seen_at, cc.last_seen_at
        ORDER BY cc.last_seen_at DESC
        """,
        (contact_id,),
    ).fetchall()
    return (
        row[0],
        [(str(i[0]), str(i[1])) for i in identities],
        [
            ContactCompanyRow(
                company_id=UUID(str(r[0])),
                canonical_name=r[1],
                kind=r[2],
                role_title=r[3],
                message_count=int(r[4]),
                first_seen_at=r[5],
                last_seen_at=r[6],
            )
            for r in companies
        ],
    )


# --------------------------------------------------------------- briefing


@dataclass(frozen=True, slots=True)
class BriefingRow:
    """One line of the landing page's "what needs you" list."""

    company_id: UUID
    canonical_name: str
    kind: str
    application_id: UUID | None
    role_title: str | None
    latest_stage: str | None
    last_message_at: datetime | None
    last_subject: str | None
    days_silent: int

    @property
    def status(self) -> str:
        return self.latest_stage or "open"


def _briefing_rows(rows: list[tuple[Any, ...]]) -> list[BriefingRow]:
    return [
        BriefingRow(
            company_id=UUID(str(r[0])),
            canonical_name=r[1],
            kind=r[2],
            application_id=UUID(str(r[3])) if r[3] else None,
            role_title=r[4],
            latest_stage=r[5],
            last_message_at=r[6],
            last_subject=r[7],
            days_silent=int(r[8] or 0),
        )
        for r in rows
    ]


def awaiting_your_reply(
    conn: psycopg.Connection[Any], *, within_days: int = 120, limit: int = 25
) -> list[BriefingRow]:
    """Companies whose last message came *in* — their ball is in your court.

    Bounded by recency on purpose. 124 companies are technically "your turn",
    but most were last touched over a year ago; an unbounded list is a backlog,
    not a briefing.
    """
    rows = conn.execute(
        """
        WITH last_message AS (
            SELECT DISTINCT ON (m.company_id)
                   m.company_id, m.direction, m.sent_at, m.subject, m.application_id
            FROM message m
            WHERE m.company_id IS NOT NULL
            ORDER BY m.company_id, m.sent_at DESC
        ),
        latest_stage AS (
            SELECT DISTINCT ON (a.company_id) a.company_id, s.stage
            FROM application a
            JOIN stage_event s ON s.application_id = a.id
            ORDER BY a.company_id, s.occurred_at DESC
        )
        SELECT c.id, c.canonical_name, c.kind, lm.application_id, a.role_title,
               ls.stage, lm.sent_at, lm.subject,
               extract(day FROM now() - lm.sent_at)::int
        FROM last_message lm
        JOIN company c ON c.id = lm.company_id
        LEFT JOIN application a ON a.id = lm.application_id
        LEFT JOIN latest_stage ls ON ls.company_id = c.id
        WHERE lm.direction = 'inbound'
          AND lm.sent_at >= now() - make_interval(days => %(within)s)
          AND (ls.stage IS NULL OR NOT (ls.stage = ANY(%(terminal)s)))
        ORDER BY lm.sent_at DESC
        LIMIT %(limit)s
        """,
        {
            "within": within_days,
            "terminal": sorted(TERMINAL_STAGES),
            "limit": limit,
        },
    ).fetchall()
    return _briefing_rows(rows)


def going_cold(
    conn: psycopg.Connection[Any],
    *,
    after_days: int = 21,
    lead_days: int = 7,
    limit: int = 25,
) -> list[BriefingRow]:
    """Applications about to be ghosted, while there is still time to act.

    The same three facts `record.is_ghosted` reads — last message outbound,
    no terminal stage, silence — asked one week early. `is_ghosted` tells you
    it already happened; this is the only view that fires before it does.
    """
    rows = conn.execute(
        """
        WITH last_message AS (
            SELECT DISTINCT ON (m.application_id)
                   m.application_id, m.direction, m.sent_at, m.subject
            FROM message m
            WHERE m.application_id IS NOT NULL
            ORDER BY m.application_id, m.sent_at DESC
        ),
        latest_stage AS (
            SELECT DISTINCT ON (application_id) application_id, stage
            FROM stage_event ORDER BY application_id, occurred_at DESC
        )
        SELECT c.id, c.canonical_name, c.kind, a.id, a.role_title,
               ls.stage, lm.sent_at, lm.subject,
               extract(day FROM now() - lm.sent_at)::int
        FROM application a
        JOIN company c ON c.id = a.company_id
        JOIN last_message lm ON lm.application_id = a.id
        LEFT JOIN latest_stage ls ON ls.application_id = a.id
        WHERE lm.direction = 'outbound'
          AND a.outcome IS NULL
          AND (ls.stage IS NULL OR NOT (ls.stage = ANY(%(terminal)s)))
          AND lm.sent_at <  now() - make_interval(days => %(warn)s)
          AND lm.sent_at >= now() - make_interval(days => %(after)s)
        ORDER BY lm.sent_at ASC
        LIMIT %(limit)s
        """,
        {
            "terminal": sorted(TERMINAL_STAGES),
            "warn": max(after_days - lead_days, 0),
            "after": after_days,
            "limit": limit,
        },
    ).fetchall()
    return _briefing_rows(rows)


#: Stages that mean a live interview process, as opposed to "applied and
#: heard nothing" or an ending. `offer` is included: an offer you have not
#: answered is the most in-flight thing there is.
_IN_FLIGHT_STAGES = (
    "recruiter_screen",
    "phone_screen",
    "technical",
    "onsite",
    "offer",
)


def in_flight(
    conn: psycopg.Connection[Any], *, limit: int = 25
) -> list[BriefingRow]:
    """Applications whose most recent stage event is mid-process."""
    rows = conn.execute(
        """
        WITH latest_stage AS (
            SELECT DISTINCT ON (application_id) application_id, stage, occurred_at
            FROM stage_event ORDER BY application_id, occurred_at DESC
        ),
        last_message AS (
            SELECT DISTINCT ON (m.application_id)
                   m.application_id, m.sent_at, m.subject
            FROM message m
            WHERE m.application_id IS NOT NULL
            ORDER BY m.application_id, m.sent_at DESC
        )
        SELECT c.id, c.canonical_name, c.kind, a.id, a.role_title,
               ls.stage, lm.sent_at, lm.subject,
               extract(day FROM now() - ls.occurred_at)::int
        FROM latest_stage ls
        JOIN application a ON a.id = ls.application_id
        JOIN company c ON c.id = a.company_id
        LEFT JOIN last_message lm ON lm.application_id = a.id
        WHERE ls.stage = ANY(%(stages)s) AND a.outcome IS NULL
        ORDER BY ls.occurred_at DESC
        LIMIT %(limit)s
        """,
        {"stages": list(_IN_FLIGHT_STAGES), "limit": limit},
    ).fetchall()
    return _briefing_rows(rows)


def in_flight_count(conn: psycopg.Connection[Any]) -> int:
    """How many applications are mid-process, ignoring `in_flight`'s row cap.

    `/home` used to render up to 25 of these as a list and then, after that
    list was replaced by a single stat tile, kept reporting `len(flight)` as
    if it were the true count — real bug, live-caught: the "N processes are
    still moving" headline silently became "25" (or whatever the cap was)
    the moment the real count crossed it, no "+" or truncation marker. This
    is the same WHERE clause with no LIMIT, so the stat is never capped by a
    number that only ever existed to bound a list nothing shows anymore.
    """
    # An application sitting at `recruiter_screen` counts as moving only if
    # you ever wrote back — the extractor mints that stage from the first
    # recruiter email, so without the gate every unanswered cold pitch in
    # the inbox reads as a live process (430 "moving" on the reference
    # record; ~100 actually were).
    row = conn.execute(
        """
        WITH latest_stage AS (
            SELECT DISTINCT ON (application_id) application_id, stage
            FROM stage_event ORDER BY application_id, occurred_at DESC
        )
        SELECT count(*)
        FROM latest_stage ls
        JOIN application a ON a.id = ls.application_id
        WHERE ls.stage = ANY(%(stages)s) AND a.outcome IS NULL
          AND (ls.stage <> 'recruiter_screen'
               OR EXISTS (SELECT 1 FROM message m
                          WHERE m.company_id = a.company_id
                            AND m.direction = 'outbound'))
        """,
        {"stages": list(_IN_FLIGHT_STAGES)},
    ).fetchone()
    return 0 if row is None else int(row[0])


def offers(conn: psycopg.Connection[Any], *, limit: int = 50) -> list[BriefingRow]:
    """Every application that ever received an offer — its own list rather
    than buried in `in_flight` (which also includes `offer` among five
    other mid-process stages, so a live offer needing a decision reads the
    same as a plain interview round in that view).

    Distinct on `application_id`, not a raw `stage_event` scan: an offer
    evidenced twice (an email, then a verbal-offer follow-up confirmation)
    is one application here, not two rows — same duplicate-evidence shape
    `Stats.interviews`' docstring documents, corrected the same way.

    `status` (`BriefingRow`'s `latest_stage`) is what tells "still waiting
    on your decision" (`offer`) apart from "already resolved"
    (`accepted`/`declined`/`rejected`) — resolving an offer writes its own
    later `stage_event`, so the latest-stage lookup below picks that up for
    free, the same as any other stage transition. Every offer ever
    received stays in this list either way; nothing here is filtered out
    once decided.

    Excludes `kind = 'agency'` companies — real bug, live-caught: a
    recruiting agency conveying "you got the offer!" news about its client
    is not itself the entity extending one, and its own `application` rows
    are a byproduct of company misattribution (the agency's domain sending
    mail about the client's process), not a second real offer. The
    employer's own row already carries this offer; the agency's is noise,
    not a duplicate to merge away.
    """
    rows = conn.execute(
        """
        WITH latest_stage AS (
            SELECT DISTINCT ON (application_id) application_id, stage, occurred_at
            FROM stage_event ORDER BY application_id, occurred_at DESC
        ),
        last_message AS (
            SELECT DISTINCT ON (m.application_id)
                   m.application_id, m.sent_at, m.subject
            FROM message m
            WHERE m.application_id IS NOT NULL
            ORDER BY m.application_id, m.sent_at DESC
        ),
        offered AS (
            SELECT DISTINCT application_id FROM stage_event WHERE stage = 'offer'
        )
        SELECT c.id, c.canonical_name, c.kind, a.id, a.role_title,
               ls.stage, lm.sent_at, lm.subject,
               extract(day FROM now() - coalesce(ls.occurred_at, lm.sent_at, now()))::int
        FROM offered o
        JOIN application a ON a.id = o.application_id
        JOIN company c ON c.id = a.company_id AND c.kind != 'agency'
        LEFT JOIN latest_stage ls ON ls.application_id = a.id
        LEFT JOIN last_message lm ON lm.application_id = a.id
        ORDER BY ls.occurred_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"limit": limit},
    ).fetchall()
    return _briefing_rows(rows)


def rejections(conn: psycopg.Connection[Any], *, limit: int = 200) -> list[BriefingRow]:
    """Every application the company said no to — its own page, mirroring
    `offers()`.

    Outcome is derived the same way `Stats.rejections` is: `application.
    outcome`, falling back to the latest outcome-bearing stage event when the
    column was never written (`_APPLICATION_FACTS`'s docstring). Not a raw
    `stage_event.stage = 'rejected'` scan, on purpose — an application can
    carry a `rejected` stage event from a form rejection and *also* a later
    human-corrected `outcome` column that disagrees (e.g. the process was
    actually reopened); the column wins when both exist, same as everywhere
    else this outcome is read.
    """
    rows = conn.execute(
        """
        WITH outcome_event AS (
            SELECT DISTINCT ON (application_id) application_id, stage
            FROM stage_event
            WHERE stage = ANY(%(outcome_stages)s)
            ORDER BY application_id, occurred_at DESC
        ),
        latest_stage AS (
            SELECT DISTINCT ON (application_id) application_id, stage, occurred_at
            FROM stage_event ORDER BY application_id, occurred_at DESC
        ),
        last_message AS (
            SELECT DISTINCT ON (m.application_id)
                   m.application_id, m.sent_at, m.subject
            FROM message m
            WHERE m.application_id IS NOT NULL
            ORDER BY m.application_id, m.sent_at DESC
        )
        SELECT c.id, c.canonical_name, c.kind, a.id, a.role_title,
               ls.stage, lm.sent_at, lm.subject,
               extract(day FROM now() - coalesce(ls.occurred_at, lm.sent_at, now()))::int
        FROM application a
        JOIN outcome_event oe ON oe.application_id = a.id
        JOIN company c ON c.id = a.company_id AND c.kind != 'agency'
        LEFT JOIN latest_stage ls ON ls.application_id = a.id
        LEFT JOIN last_message lm ON lm.application_id = a.id
        WHERE coalesce(a.outcome, oe.stage) = 'rejected'
        ORDER BY ls.occurred_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"outcome_stages": list(_OUTCOME_STAGES), "limit": limit},
    ).fetchall()
    return _briefing_rows(rows)


# ----------------------------------------------------------------- ingest


@dataclass(frozen=True, slots=True)
class IngestHealth:
    """What the ingest log says, for `/pipeline`.

    `ingest_run` has 589 rows and no surface at all today — which is the
    problem migration 0004 was written to solve ("a failure with nobody
    watching is a failure nobody finds") and then only half solved.
    """

    runs: int
    failures: int
    last_run_at: datetime | None
    days_covered: int
    first_day: date | None
    last_day: date | None
    #: Days inside the covered span with no successful run — a backfill that
    #: quietly skipped a day looks identical to a quiet day without this.
    missing_days: int
    recent: list[tuple[Any, ...]]


def ingest_health(conn: psycopg.Connection[Any], *, limit: int = 20) -> IngestHealth:
    summary = conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE failure IS NOT NULL),
               max(started_at),
               count(DISTINCT day) FILTER (WHERE day IS NOT NULL AND failure IS NULL),
               min(day), max(day)
        FROM ingest_run
        """
    ).fetchone()
    assert summary is not None
    first_day, last_day = summary[4], summary[5]
    missing = 0
    if first_day and last_day:
        span = (last_day - first_day).days + 1
        missing = max(span - int(summary[3]), 0)
    recent = conn.execute(
        "SELECT source, account, day, started_at, finished_at, fetched, stored,"
        " rows_inserted, failure FROM ingest_run"
        " ORDER BY started_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return IngestHealth(
        runs=int(summary[0]),
        failures=int(summary[1]),
        last_run_at=summary[2],
        days_covered=int(summary[3]),
        first_day=first_day,
        last_day=last_day,
        missing_days=missing,
        recent=list(recent),
    )


# ---------------------------------------------------------------- one message


@dataclass(frozen=True, slots=True)
class MessageDetail:
    """One message, opened. The expanded half of `CommunicationRow`.

    Fetched on demand rather than joined into every list row: bodies average
    3.5k characters and `/messages` lists thousands of rows, so inlining them
    would send megabytes to render a page of subjects.
    """

    id: UUID
    subject: str | None
    sent_at: datetime
    direction: Direction
    channel: str
    account: str
    sender_address: str | None
    recipient_addresses: tuple[str, ...]
    thread_id: str | None
    body_text: str
    classified_by: str | None
    company_id: UUID | None
    company_name: str | None
    contact_id: UUID | None
    contact_name: str | None
    contact_address: str | None
    #: (stage, role_title) for every claim citing this message as evidence.
    evidences: tuple[tuple[str, str | None], ...]

    @property
    def tag(self) -> str:
        return classification_tag(self.classified_by)

    @property
    def correspondent(self) -> str | None:
        """The address to teach a rule about.

        `sender_address` (migration 0007) is the right source and is empty for
        every message ingested before that column existed — 58k of them here —
        so the contact identity is the working fallback. Both are the same
        address when both are present.
        """
        return self.sender_address or self.contact_address


def message_detail(
    conn: psycopg.Connection[Any], message_id: UUID
) -> MessageDetail | None:
    row = conn.execute(
        """
        SELECT m.id, m.subject, m.sent_at, m.direction, m.channel, m.account,
               m.sender_address, m.recipient_addresses, m.thread_id,
               m.body_text, m.classified_by, m.company_id, c.canonical_name,
               m.contact_id, ct.display_name, ci.identifier,
               coalesce(
                   (SELECT array_agg(ARRAY[se.stage, coalesce(a.role_title, '')])
                      FROM stage_event se
                      LEFT JOIN application a ON a.id = se.application_id
                     WHERE se.evidence_message_id = m.id),
                   '{}'
               ) AS evidences
        FROM message m
        LEFT JOIN company c ON c.id = m.company_id
        LEFT JOIN contact ct ON ct.id = m.contact_id
        LEFT JOIN contact_identity ci
          ON ci.contact_id = m.contact_id AND ci.channel = m.channel
        WHERE m.id = %s
        """,
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    return MessageDetail(
        id=UUID(str(row[0])),
        subject=row[1],
        sent_at=row[2],
        direction=row[3],
        channel=row[4],
        account=row[5],
        sender_address=row[6],
        recipient_addresses=tuple(row[7] or ()),
        thread_id=row[8],
        body_text=row[9] or "",
        classified_by=row[10],
        company_id=UUID(str(row[11])) if row[11] else None,
        company_name=row[12],
        contact_id=UUID(str(row[13])) if row[13] else None,
        contact_name=row[14],
        contact_address=row[15],
        evidences=tuple((e[0], e[1] or None) for e in (row[16] or ())),
    )


def count_communications(
    conn: psycopg.Connection[Any],
    *,
    company_id: UUID | None = None,
    channel: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    stage_evidencing: bool | None = None,
    tag: str | None = None,
    has_company: bool | None = None,
    hide_negative: bool = True,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """How many messages match, ignoring the page window.

    Deliberately a `count(*)` over the same predicates rather than
    `len(list_communications(...))`: the point of paging 58k rows is not
    fetching them, and a total computed by fetching everything would undo it.
    `hide_negative` must default the same way `list_communications`' does —
    a count run against a different predicate than the rows it is meant to
    paginate is just a wrong number.
    """
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if company_id is not None:
        clauses.append(
            "(m.company_id = %(company_id)s OR EXISTS ("
            "SELECT 1 FROM message_company mc2 WHERE mc2.message_id = m.id"
            " AND mc2.role = 'agency' AND mc2.company_id = %(company_id)s))"
        )
        params["company_id"] = company_id
    if channel is not None:
        clauses.append("m.channel = %(channel)s")
        params["channel"] = channel
    if direction is not None:
        clauses.append("m.direction = %(direction)s")
        params["direction"] = direction
    if since is not None:
        clauses.append("m.sent_at >= %(since)s")
        params["since"] = since
    if until is not None:
        clauses.append("m.sent_at <= %(until)s")
        params["until"] = until
    if search:
        clauses.append("m.search_tsv @@ websearch_to_tsquery('english', %(search)s)")
        params["search"] = search
    if stage_evidencing is not None:
        exists = "SELECT 1 FROM stage_event se WHERE se.evidence_message_id = m.id"
        clauses.append(
            f"EXISTS ({exists})" if stage_evidencing else f"NOT EXISTS ({exists})"
        )
    if tag in _TAG_SQL:
        clauses.append(_TAG_SQL[tag])
    if hide_negative:
        clauses.append(f"NOT {NEGATIVE_SQL}")
    if has_company is not None:
        clauses.append(
            "m.company_id IS NOT NULL" if has_company else "m.company_id IS NULL"
        )
    row = conn.execute(
        f"SELECT count(*) FROM message m WHERE {' AND '.join(clauses)}", params
    ).fetchone()
    return 0 if row is None else int(row[0])


# ----------------------------------------------------------------- triage


def pending_reviews(
    conn: psycopg.Connection[Any],
    *,
    reason: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The review queue, newest first, with the evidence message's subject.

    `reason` groups the queue by *why* the extractor was unsure. 1,394 of the
    1,716 open items share one reason string, which is what makes deciding a
    whole bucket at once the realistic action rather than a shortcut.
    """
    clauses = ["rq.status = 'pending'"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if reason:
        clauses.append("rq.reason = %(reason)s")
        params["reason"] = reason
    rows = conn.execute(
        f"""
        SELECT rq.id, rq.message_id, rq.extraction, rq.confidence, rq.reason,
               rq.extracted_by, rq.created_at, m.subject, m.sent_at, m.direction
        FROM review_queue rq
        JOIN message m ON m.id = rq.message_id
        WHERE {' AND '.join(clauses)}
        ORDER BY rq.created_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return [
        {
            "id": UUID(str(r[0])),
            "message_id": UUID(str(r[1])),
            "extraction": r[2] or {},
            "confidence": float(r[3]) if r[3] is not None else None,
            "reason": r[4],
            "extracted_by": r[5],
            "created_at": r[6],
            "subject": r[7],
            "sent_at": r[8],
            "direction": r[9],
        }
        for r in rows
    ]


def pending_reviews_count(conn: psycopg.Connection[Any], *, reason: str | None = None) -> int:
    """How many `pending_reviews` rows match, ignoring LIMIT/OFFSET — the
    other half of "48 shown, 1,716 total" the same way `count_companies` is
    for `/companies`.

    Its absence was a real bug, live-caught by browsing the app: the queue
    tab paginates server-side (`PAGE_SIZE`, `web/api.py`) but had nothing to
    build a page count or a "next" control from, so anything past page 1
    (item 50) was permanently unreachable with no indication more existed.
    """
    clauses = ["rq.status = 'pending'"]
    params: dict[str, Any] = {}
    if reason:
        clauses.append("rq.reason = %(reason)s")
        params["reason"] = reason
    row = conn.execute(
        f"SELECT count(*) FROM review_queue rq WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()
    return 0 if row is None else int(row[0])


def review_reasons(conn: psycopg.Connection[Any]) -> list[tuple[str, int]]:
    """Open review items grouped by reason — the queue's own shape."""
    rows = conn.execute(
        "SELECT reason, count(*) FROM review_queue WHERE status = 'pending'"
        " GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows]


def list_rules(
    conn: psycopg.Connection[Any],
    *,
    source: str | None = None,
    verdict: str | None = None,
) -> list[dict[str, Any]]:
    """Learned sender rules, with the company a positive rule points at."""
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if source in ("human", "auto"):
        clauses.append("sr.source = %(source)s")
        params["source"] = source
    if verdict in ("positive", "negative", "undecided"):
        clauses.append("coalesce(sr.verdict, sc.verdict) = %(verdict)s")
        params["verdict"] = verdict
    rows = conn.execute(
        f"""
        SELECT sr.id, sr.match_type, sr.value,
               coalesce(sr.verdict, sc.verdict) AS verdict,
               sr.category, sr.source, sr.created_at,
               c.id, c.canonical_name
        FROM sender_rule sr
        LEFT JOIN sender_category sc ON sc.category = sr.category
        LEFT JOIN company c ON c.id = coalesce(sr.company_id, sc.company_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY sr.created_at DESC
        """,
        params,
    ).fetchall()
    return [
        {
            "id": UUID(str(r[0])),
            "match_type": r[1],
            "value": r[2],
            "verdict": r[3],
            "category": r[4],
            "source": r[5],
            "created_at": r[6],
            "company_id": UUID(str(r[7])) if r[7] else None,
            "company_name": r[8],
        }
        for r in rows
    ]


def list_categories(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    """Named sender groups and how many rules each one tags.

    Editing one row here re-decides every sender in the group at once — the
    leverage migration 0009 exists for, and which has had no surface until now.
    """
    rows = conn.execute(
        """
        SELECT sc.category, sc.verdict, c.canonical_name,
               count(sr.id) AS rule_count
        FROM sender_category sc
        LEFT JOIN sender_rule sr ON sr.category = sc.category
        LEFT JOIN company c ON c.id = sc.company_id
        GROUP BY sc.category, sc.verdict, c.canonical_name
        ORDER BY rule_count DESC, sc.category
        """
    ).fetchall()
    return [
        {
            "category": r[0],
            "verdict": r[1],
            "company_name": r[2],
            "rule_count": int(r[3]),
        }
        for r in rows
    ]


def list_verifications(
    conn: psycopg.Connection[Any], *, only_flagged: bool = False, limit: int = 100
) -> list[dict[str, Any]]:
    """Audit passes, flagged first.

    `company_id` is nullable and left dangling on purpose (migration 0016): a
    company confirmed not job-related is deleted, and the audit row that
    explains why has to outlive it. Those rows show as "(removed)" rather than
    being hidden, because the explanation is the point.
    """
    where = " WHERE v.classification_correct IS NOT TRUE" if only_flagged else ""
    rows = conn.execute(
        f"""
        SELECT v.id, v.company_id, c.canonical_name, v.model, v.message_count,
               v.classification_correct, v.verified_name, v.verified_kind,
               v.reasoning, v.suggested_rules, v.created_at
        FROM company_verification v
        LEFT JOIN company c ON c.id = v.company_id
        {where}
        ORDER BY v.classification_correct NULLS FIRST, v.created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": UUID(str(r[0])),
            "company_id": UUID(str(r[1])) if r[1] else None,
            "company_name": r[2],
            "model": r[3],
            "message_count": int(r[4]),
            "classification_correct": r[5],
            "verified_name": r[6],
            "verified_kind": r[7],
            "reasoning": r[8],
            "suggested_rules": r[9] or [],
            "created_at": r[10],
        }
        for r in rows
    ]


def triage_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Badge numbers for the triage tabs and the nav rail."""
    row = conn.execute(
        """
        SELECT (SELECT count(*) FROM review_queue WHERE status = 'pending'),
               (SELECT count(*) FROM sender_rule),
               (SELECT count(*) FROM company_verification),
               (SELECT count(*) FROM company_verification
                 WHERE classification_correct IS NOT TRUE),
               (SELECT count(*) FROM company c
                 WHERE NOT EXISTS (SELECT 1 FROM company_verification v
                                    WHERE v.company_id = c.id))
        """
    ).fetchone()
    assert row is not None
    return {
        "pending": int(row[0]),
        "rules": int(row[1]),
        "verifications": int(row[2]),
        "flagged": int(row[3]),
        "unverified": int(row[4]),
    }


def sender_graph_coverage(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """How much of the correspondent graph (migration 0007) is populated.

    The senders tab needs this to be honest. `sender_domain`/`sender_address`
    are written by `ingest_account` from the parsed envelope, but every
    message ingested before migration 0007 landed carries NULL — and a
    fanout preview reading 0 because the column is empty looks identical to
    one reading 0 because the rule is new. Re-deriving from raw storage
    (`jobd rebuild`) is what fills them in.
    """
    row = conn.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE sender_domain IS NOT NULL),
               count(*) FILTER (WHERE thread_id IS NOT NULL),
               count(*) FILTER (WHERE classified_at IS NULL)
        FROM message
        """
    ).fetchone()
    assert row is not None
    return {
        "total": int(row[0]),
        "with_sender": int(row[1]),
        "with_thread": int(row[2]),
        "unclassified": int(row[3]),
    }
