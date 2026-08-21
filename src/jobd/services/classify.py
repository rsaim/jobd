"""Classification: raw bytes → an evidence-linked record (PRD P2, G2).

The pipeline, and the order is the design:

    metadata  →  pre-filter  →  [LLM]  →  label gate  →  record | queue
                      ↑                       ↑
                   makes I4 true          makes P2 true

**The pre-filter runs first, always, and reads metadata only** — sender/
recipient domains and addresses, thread membership, Gmail labels, bulk-mail
headers, and learned rules. It never reads subject or body; that judgment is
the LLM's. A message it drops never reaches the model, so on the cloud path it
never leaves the machine. That is the mechanism behind I4; without it the
invariant is a sentence in a document.

**Fanout is how the pre-filter learns.** One human decision about an
attribute (a domain, an address) — via `sender_rule` — resolves every message
that shares it, past and future: the backlog via `bulk_resolve`/
`bulk_negative`, everything ingested after via `prefilter.score`'s `learned`
argument. See classify_pending's docstring on where the rule set is loaded.

**The label gate runs last.** `extraction.label` is one of three buckets —
"positive", "negative", "unclassified" — not a continuous score to threshold.
"unclassified" (or "positive" with no company identifiable) goes to the
review queue instead of the record: a pending item means the system noticed
something and declined to act on it, which is a very different state from
having guessed.

Nothing here is destructive. Re-running over an unclassified mailbox produces
the same record; re-running after `clear_classification` re-derives it from raw
(I3). Entity resolution biases toward splitting, so the worst case is two
company pages a human joins with one alias — not two hiring processes fused
into a timeline that never happened.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

import psycopg

from jobd.domain import prefilter
from jobd.domain.envelope import body_text
from jobd.domain.extraction import (
    EXTRACTION_SCHEMA,
    PROMPT,
    THREAD_EXTRACTION_SCHEMA,
    THREAD_PROMPT,
    Extraction,
    from_payload,
    render_for_model,
    render_thread_for_model,
)
from jobd.domain.raw import RawMessage
from jobd.domain.record import (
    Application,
    Company,
    CompanyAlias,
    CompanyKind,
    Contact,
    ContactIdentity,
    Message,
    SenderRule,
    StageEvent,
)
from jobd.domain.resolve import (
    ApplicationWindow,
    normalise_company,
    normalise_domain,
    pick_application,
)
from jobd.ports import LLMProvider, Storage

# Narrow protocols rather than the concrete repository classes. Each names
# exactly what this service uses, so the type checker can see the return types
# — and so the dependency is legible: a reader can tell at a glance that
# classification never reads a company's aliases or resolves a review.


class CompanyStore(Protocol):
    def by_domain(self, domain: str) -> Company | None: ...
    def by_alias(self, alias: str) -> Company | None: ...
    def add(self, company: Company) -> Company: ...
    def add_alias(self, alias: CompanyAlias) -> CompanyAlias: ...
    def widen(self, company_id: UUID | None, when: datetime) -> None: ...
    def all(self) -> list[Company]: ...


class ApplicationStore(Protocol):
    def for_company(self, company_id: UUID) -> list[Application]: ...
    def add(self, application: Application) -> Application: ...
    def set_role(self, application_id: UUID | None, role_title: str) -> None: ...
    def close(
        self, application_id: UUID | None, *, outcome: str, ended_at: datetime
    ) -> None: ...


class ContactStore(Protocol):
    def by_identity(self, channel: str, identifier: str) -> Contact | None: ...
    def add(self, contact: Contact) -> Contact: ...
    def add_identity(self, identity: ContactIdentity) -> ContactIdentity: ...
    def link_company(
        self,
        contact_id: UUID,
        company_id: UUID,
        *,
        role_title: str | None,
        first_seen_at: datetime,
        last_seen_at: datetime,
    ) -> None: ...


class MessageStore(Protocol):
    def unclassified(
        self, limit: int = ..., partition: tuple[int, int] | None = ...
    ) -> list[Message]: ...
    def unembedded(self, limit: int = ...) -> list[Message]: ...
    def thread_context(
        self, thread_id: str
    ) -> tuple[UUID, UUID | None, UUID | None, UUID | None] | None: ...
    def mark_classified(self, message_id: UUID, by: str) -> None: ...
    def set_body(self, message_id: UUID, text: str) -> None: ...
    def set_embedding(self, message_id: UUID, vector: list[float]) -> None: ...
    def add(self, message: Message) -> Message: ...
    def link(
        self,
        message_id: UUID,
        *,
        company_id: UUID | None = ...,
        application_id: UUID | None = ...,
        contact_id: UUID | None = ...,
    ) -> None: ...


class SenderRuleStore(Protocol):
    def all(self) -> list[SenderRule]: ...
    def add(self, rule: SenderRule) -> SenderRule: ...


class MessageCompanyStore(Protocol):
    def link(self, message_id: UUID, company_id: UUID, *, role: str) -> None: ...


class StageStore(Protocol):
    def add(self, event: StageEvent) -> StageEvent: ...


class ReviewStore(Protocol):
    def enqueue(
        self,
        message_id: UUID,
        *,
        extraction: dict[str, Any],
        reason: str,
        extracted_by: str,
        # Optional and unwritten going forward — `Extraction` no longer
        # carries a numeric confidence (label-based routing, see the module
        # docstring). The column stays nullable rather than dropped, so
        # historical rows keep their real values (I3: re-derive, don't erase).
        confidence: float | None = None,
    ) -> UUID: ...
    def pending(self, limit: int = ...) -> list[dict[str, Any]]: ...
    def count_pending(self) -> int: ...
    def resolve(self, review_id: UUID, status: str) -> None: ...
    def resolve_by_message(self, message_id: UUID) -> None: ...


class Repositories(Protocol):
    """The repository set this service needs. Assembled by the caller."""

    companies: CompanyStore
    contacts: ContactStore
    applications: ApplicationStore
    messages: MessageStore
    stages: StageStore
    reviews: ReviewStore
    sender_rules: SenderRuleStore
    message_companies: MessageCompanyStore


@dataclass(slots=True)
class ClassifyResult:
    """Counts for one run. Every branch of the pipeline is visible."""

    seen: int = 0
    #: Deterministically negative (prefilter.Status). Never reached any
    #: extractor — on the cloud path, the number of messages that did *not*
    #: leave the machine.
    filtered_out: int = 0
    #: Deterministically positive (prefilter.Status). Resolved by the
    #: deterministic extractor, not the configured model — see `_one`'s
    #: routing. Zero if no `deterministic` provider was passed to
    #: `classify_pending`, in which case positives fall through to `llm_calls`
    #: like everything undecided, unchanged from before this counter existed.
    deterministic_calls: int = 0
    #: Linked straight from an already-recorded sibling in the same thread —
    #: no extractor call at all, deterministic or paid. See `_one`'s
    #: thread-carry-forward branch.
    carried_forward: int = 0
    llm_calls: int = 0
    #: Of `llm_calls`, how many got a second, bigger-model opinion because
    #: the first was ambiguous — see `classify_pending`'s `escalation_llm`.
    #: Zero whenever no escalation model was configured.
    escalated: int = 0
    not_job_related: int = 0
    queued_for_review: int = 0
    recorded: int = 0
    companies_created: int = 0
    applications_created: int = 0
    stage_events: int = 0
    #: New `sender_rule` rows written automatically from a confident
    #: extraction's own `company_domain` — the online-learning half of the
    #: fanout, closing the loop the manual `learn` command started this
    #: session: a human no longer has to notice a domain and teach it by
    #: hand before the next message from it stops paying for an extractor
    #: call. See `_record`'s docstring for why it teaches `undecided`, not
    #: `positive`.
    rules_learned: int = 0
    #: Messages resolved from a thread's already-stored model reading — the
    #: one-call-per-thread cache (`thread_extraction`) answering instead of
    #: a second paid call on a conversation the model has already read.
    thread_llm_reused: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def disclosed(self) -> int:
        """Messages sent to a model. Zero on a local provider, by definition
        of `is_local`, but the count is what makes the claim checkable.
        Includes `escalated` — a second call is a second disclosure of the
        same content, to a different model."""
        return self.llm_calls + self.escalated


def classify_pending(
    *,
    storage: Storage,
    llm: LLMProvider,
    repos: Repositories,
    conn: psycopg.Connection[Any],
    limit: int = 500,
    embed: bool = False,
    deterministic: LLMProvider | None = None,
    escalation_llm: LLMProvider | None = None,
    fetch_workers: int = 32,
    partition: tuple[int, int] | None = None,
    meter: Any = None,
) -> ClassifyResult:
    """Classify unclassified messages. Returns counts, never raises for one.

    Args:
        limit: Batch size. A five-year mailbox is classified in batches so a
            failure costs one batch, not the run.
        embed: Compute embeddings. Off by default because it doubles the model
            calls.
        deterministic: Extractor for `prefilter.Status == "positive"`
            messages — an ATS-domain hit is job-related by construction, so
            the question left is *what*, and a template-aware rule-based
            extractor answers that as well as a paid model does for this
            narrow, structured case (see rulebased.py's own docstring on
            "where the rules are sure"). `None` means every non-negative
            message goes to `llm`, unchanged from before this parameter
            existed.
        escalation_llm: A second, presumably bigger/pricier model tried only
            when `llm` (or `deterministic`) comes back ambiguous — the same
            case that would otherwise go straight to `review_queue`. `None`
            (the default) skips this entirely: no second call, unchanged
            from before this parameter existed. Its answer replaces the
            first one outright, including a second "unclassified" — it's a
            second opinion, not a vote.
        fetch_workers: Thread-pool size for the S3 prefetch below. The fetch
            is I/O-bound (network round trip, no CPU work), so a pool of
            blocking `storage.get` calls buys real parallelism even on a
            2-core box — this is exactly the profile `ThreadPoolExecutor`
            fits, unlike CPU-bound work where the GIL would negate it.

    Loads the learned sender-rule set once, up front — not per message. The
    rule set is small (dozens to low hundreds of rows); one query per batch
    beats one per message by orders of magnitude, and a rule learned mid-batch
    still applies to the next batch, not this one — an acceptable staleness
    window given `bulk_resolve`/`bulk_negative` already swept the backlog for
    that same rule the moment it was learned (see `cli.main`'s learn command).

    The S3 fetch for the whole batch runs concurrently, up front — that is
    the actual bottleneck (network round trip per message), not the
    deterministic rule evaluation that follows. Postgres writes stay
    sequential, one message at a time with a commit per message, same as
    before: `psycopg` connections are not thread-safe, and the DB work here
    is fast enough (local, WAL-buffered) that serializing it costs far less
    than the round trip it follows.
    """
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    result = ClassifyResult()
    rules = repos.sender_rules.all()
    # Seeded from the loaded rule set, then grown in place as `_record`
    # auto-teaches new domains — the gate that stops a confident extraction
    # from re-teaching (and re-committing) the same domain on every one of
    # the many messages a real company sends. A domain learned mid-batch is
    # visible to the rest of *this* batch immediately (unlike `rules` above,
    # which is a snapshot); the next batch reloads `rules` fresh regardless.
    taught_domains = {r.value.lower() for r in rules if r.match_type == "domain"}
    messages = repos.messages.unclassified(limit, partition=partition)
    raws = _prefetch(storage, messages, fetch_workers)

    # One commit for the whole batch, not one per message — the WAL fsync
    # per commit was real, serialized cost on top of every write. Per-message
    # isolation still holds: `conn.transaction()` opens a SAVEPOINT (psycopg
    # does this automatically when already inside a transaction), so one bad
    # message rolls back only its own writes and the loop carries on inside
    # the same outer transaction. A crash mid-batch loses at most this batch
    # — cheap, since re-deriving it is exactly what I3 promises.
    for message in messages:
        result.seen += 1
        try:
            raw = raws[message.storage_key]
            if isinstance(raw, Exception):
                raise raw
            with conn.transaction():
                _one(
                    message,
                    raw=raw,
                    llm=llm,
                    deterministic=deterministic,
                    repos=repos,
                    rules=rules,
                    taught_domains=taught_domains,
                    embed=embed,
                    out=result,
                    escalation_llm=escalation_llm,
                )
        except Exception as exc:  # one bad message must not cost the batch
            result.errors.append(f"{message.storage_key}: {type(exc).__name__}: {exc}")
            meter.error(f"{message.storage_key}: {type(exc).__name__}: {exc}")
        meter.bump()
        if partition is not None:
            # Concurrent partitions write shared rows — the company table,
            # and thread siblings that live in another slice. A batch-long
            # transaction holds those row locks for minutes and serializes
            # every other partition behind it (live-caught: 169s
            # `transactionid` waits, eight workers advancing at single-worker
            # speed). Per-message commits trade WAL fsyncs for lock hold
            # times in the milliseconds; the fsync cost only matters in the
            # single-process case, which keeps the batch commit below.
            conn.commit()
    conn.commit()
    meter.flush(force=True)
    return result


def mirror_classify(meter: Any, result: ClassifyResult) -> None:
    """Copy accumulated branch counters onto the live metrics row. Called by
    the CLI with its *running totals* (one meter spans many batches), so the
    values are absolute mirrors, idempotent and order-safe — never per-batch
    deltas, which would reset the row every 500 messages."""
    for key, value in (
        ("prefilter_negative", result.filtered_out),
        ("rulebased", result.deterministic_calls),
        ("thread_carry", result.carried_forward),
        ("llm_calls", result.llm_calls),
        ("escalated", result.escalated),
        ("not_job_related", result.not_job_related),
        ("queued_for_review", result.queued_for_review),
        ("recorded", result.recorded),
        ("companies_created", result.companies_created),
        ("rules_learned", result.rules_learned),
        ("thread_llm_reused", result.thread_llm_reused),
    ):
        meter.set_counter(key, value)


def backfill_embeddings(
    *,
    llm: LLMProvider,
    repos: Repositories,
    conn: psycopg.Connection[Any],
    limit: int = 2000,
    batch_size: int = 100,
) -> int:
    """Embed already-recorded messages that predate `classify --embed`, or
    ran before it was ever turned on — every message in the mailbox until
    this feature existed. `classify_pending`'s `embed` flag only ever fires
    once, at a message's first classification (`_one`'s `_record` call);
    this is the one-off catch-up for everything that missed that moment,
    not part of the steady-state pipeline. Semantic search
    (`tools/registry.py`'s `search_communications`, mode="semantic") can
    only find what this (or `--embed`) has actually embedded.

    Batched — `batch_size` texts per `llm.embed()` call, not one call per
    message — same "the round trip is the cost, not the compute" reasoning
    `_prefetch` above documents for the S3 fetch, applied to an embedding
    API instead. Commits per batch, not per message or once at the end: a
    crash mid-run loses at most one batch, and `unembedded()`'s query
    naturally excludes everything already committed on the next call.

    Returns how many were embedded this call — 0 means the queue is empty,
    the signal `cli.main`'s `--all` loop stops on.
    """
    messages = repos.messages.unembedded(limit)
    embedded = 0
    for i in range(0, len(messages), batch_size):
        batch = messages[i : i + batch_size]
        # Same slice `_one`'s own `embed` branch uses — one embedding is a
        # gist of the message, not a full-fidelity index of it; 2000 chars
        # is plenty for that and keeps a giant thread from dominating the
        # request. The embed API rejects an empty string outright (found
        # live: one blank-body message 400'd an entire 100-message batch)
        # — a handful of recorded messages have `body_text = ''` (extraction
        # found no plaintext), so fall back to the subject, then a literal
        # placeholder, rather than either crashing the batch or leaving
        # these permanently unembedded (`unembedded()` would keep
        # reselecting them forever, an infinite `--all` loop).
        texts = [m.body_text[:2000].strip() or m.subject or "(no content)" for m in batch]
        vectors = llm.embed(texts)
        for message, vector in zip(batch, vectors, strict=True):
            repos.messages.set_embedding(message.id, vector)
        conn.commit()
        embedded += len(batch)
    return embedded


def _prefetch(
    storage: Storage, messages: list[Message], workers: int
) -> dict[str, RawMessage | Exception]:
    """Fetch every message's raw bytes concurrently, ahead of the sequential
    write loop. A failed fetch is stored as its exception rather than
    raised here, so one bad key still costs only its own message in the
    caller's per-message try/except — not the whole batch."""
    keys = {m.storage_key for m in messages}
    out: dict[str, RawMessage | Exception] = {}
    if not keys:
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(storage.get, key): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                out[key] = future.result()
            except Exception as exc:  # reported per-message by the caller
                out[key] = exc
    return out


#: The latest message of a long thread carries the whole quoted history once;
#: past this many characters it's boilerplate and signature blocks, not new
#: evidence. Keeps the one paid call per thread token-bounded.
_THREAD_BODY_CAP = 15_000


def _thread_llm_payload(
    llm: LLMProvider,
    repos: Repositories,
    *,
    thread_id: str,
    rules_context: str = "",
    fallback_prompt: str,
    fallback_rendered: str,
) -> tuple[dict[str, Any], bool]:
    """One paid model call per thread, ever. Returns (payload, reused).

    The thread's stored reading answers first — a conversation the model has
    already read is never sent again. On a miss, the thread's latest stored
    message (which quotes everything before it) is rendered with its full
    envelope metadata plus three thread-level header lines, read once under
    `THREAD_PROMPT`, and the payload is upserted to `thread_extraction` so
    every sibling — in this run, a partition worker's run, or the review
    sweep — resolves from the row.

    Two partition workers racing the same thread can each pay once; the
    upsert makes that benign (last write wins), same as the thread-carry
    race classify already tolerates. The single-message fallback only fires
    when the thread has no stored body at all — possible mid-ingest, and
    worth a working answer over a blank render.
    """
    stored = repos.messages.thread_extraction(thread_id)
    if stored is not None:
        return dict(stored["payload"]), True
    latest = repos.messages.thread_latest(thread_id)
    if latest is None:
        return llm.extract(fallback_prompt, EXTRACTION_SCHEMA, text=fallback_rendered), False
    rendered = render_thread_for_model(
        sender=latest["sender"],
        recipient=latest["recipients"],
        subject=latest["subject"],
        body=latest["body"][:_THREAD_BODY_CAP],
        cc=latest["cc"],
        reply_to=latest["reply_to"],
        date=str(latest["sent_at"]) if latest["sent_at"] else None,
        labels=latest["labels"],
        thread_messages=latest["thread_messages"],
        thread_senders=latest["thread_senders"],
        first_date=latest["first_date"],
    )
    prompt = (rules_context + "\n" + THREAD_PROMPT) if rules_context else THREAD_PROMPT
    payload = llm.extract(prompt, THREAD_EXTRACTION_SCHEMA, text=rendered)
    repos.messages.save_thread_extraction(
        thread_id,
        latest_message_id=latest["id"],
        covers_sent_at=latest["sent_at"],
        model=llm.name,
        payload=payload,
        messages_covered=latest["thread_messages"],
        reasoning=getattr(llm, "last_reasoning", None),
    )
    return payload, False


def _one(
    message: Message,
    *,
    raw: RawMessage,
    llm: LLMProvider,
    deterministic: LLMProvider | None,
    repos: Repositories,
    rules: list[SenderRule],
    taught_domains: set[str],
    embed: bool,
    out: ClassifyResult,
    escalation_llm: LLMProvider | None = None,
) -> None:
    assert message.id is not None
    payload, raw_metadata = raw.payload, dict(raw.metadata)
    text = body_text(payload)
    # Skip the write when unchanged (I3: re-deriving is routine, so most
    # `--reclassify` reruns see the same text they already stored). The
    # column drives a generated tsvector, so an unconditional UPDATE here
    # was paying full index maintenance on every message, every rerun, even
    # when nothing about the derived text had changed — the actual cost
    # behind a "just applying deterministic rules" batch feeling I/O-bound.
    if message.body_text != text:
        repos.messages.set_body(message.id, text)

    sender = _header(payload, "from")
    recipients = _header(payload, "to")
    cc = _header(payload, "cc")
    reply_to = _header(payload, "reply-to")
    date = _header(payload, "date")
    thread_id = message.thread_id or raw_metadata.get("thread_id") or ""
    thread_ctx = repos.messages.thread_context(thread_id) if thread_id else None
    if thread_ctx is None:
        # RFC fallback (0026): sources without a native thread id (LinkedIn
        # pushes, archives) still chain by In-Reply-To → Message-ID.
        parent_ref = _header(payload, "in-reply-to")
        if parent_ref and hasattr(repos.messages, "rfc_parent_context"):
            thread_ctx = repos.messages.rfc_parent_context(parent_ref)
    known_contact = _known_contact(sender, recipients, message.account, repos)
    learned, learned_company_id, learned_category, learned_domain = _match_rule(
        rules, sender, recipients
    )
    # The sender matched a RECRUITING_AGENCY-tagged rule: whatever company
    # this message resolves to, that domain is the agency, not the employer
    # — carried through to `_resolve_company`'s no-client fallback and
    # `_record`'s secondary-link write, never into `extraction` itself (so
    # it never grows `taught_domains`/gets auto-taught as a plain company
    # domain — see `_is_hardcoded_domain`'s docstring on why that matters).
    agency_domain = (
        learned_domain
        if learned_category == prefilter.SenderCategory.RECRUITING_AGENCY
        else None
    )
    verdict = prefilter.score(
        sender=sender,
        recipients=recipients,
        known_contact=known_contact,
        thread_positive=thread_ctx is not None,
        learned=learned,
        gmail_labels=raw_metadata.get("labels", ""),
        has_list_unsubscribe=_is_bulk(payload),
    )
    if verdict.status == "negative":
        out.filtered_out += 1
        repos.messages.mark_classified(message.id, f"prefilter:{verdict.status}")
        return

    # Zero-cost path: some other message in this exact conversation chain, or
    # a human-confirmed rule about this sender's domain/address, already
    # settled the company — so this one's company/application/contact are
    # already known facts, not a guess an extractor would have to make from
    # one message alone. No extractor call at all, of either kind. The 4th
    # element is the secondary agency link, if the thread (or, for a fresh
    # rule-carry, nothing yet) already established one.
    carried = thread_ctx or (
        (learned_company_id, None, None, None)
        if learned == "positive" and learned_company_id is not None
        else None
    )
    if verdict.status == "positive" and carried is not None:
        company_id, application_id, contact_id, agency_company_id = carried
        repos.messages.link(
            message.id,
            company_id=company_id,
            application_id=application_id,
            contact_id=contact_id,
        )
        if agency_company_id is not None:
            repos.message_companies.link(
                message.id, agency_company_id, role="agency"
            )
        out.carried_forward += 1
        out.recorded += 1
        by = f"thread-carry:{thread_id}" if thread_ctx else "rule-carry"
        repos.messages.mark_classified(message.id, by)
        # A stale pending review item for this exact message — e.g. left
        # over from before a rule/thread made it resolvable for free — is
        # now moot. Closing it is what stops it lingering forever with
        # extraction/reason text that no longer matches reality.
        repos.reviews.resolve_by_message(message.id)
        return

    # positive -> the deterministic extractor, if one was given (see
    # classify_pending's docstring on why this is the actual saving the
    # status taxonomy exists for). undecided, or no deterministic extractor
    # configured, -> the real model, same as before status existed.
    extractor = deterministic if verdict.status == "positive" and deterministic else llm
    if extractor is llm:
        out.llm_calls += 1
    else:
        out.deterministic_calls += 1

    prompt = PROMPT
    rules_context = ""
    if verdict.status == "undecided" and extractor.name != "rulebased":
        # Few-shot context from prior confirmed decisions in the same
        # category — see `learned_rules_context`'s docstring. Only for a
        # real model: RuleBasedProvider ignores the prompt entirely (pure
        # regex), so building this for it would be pure waste. Only for
        # `undecided`: a `positive` message already skipped this branch via
        # `carried`, or is going to the free deterministic extractor.
        section = "RECRUITING_AGENCY" if agency_domain else "Direct employers"
        rules_context = learned_rules_context(section)
        if rules_context:
            prompt = rules_context + "\n" + prompt

    # Built once, reused for the escalation call below too — same message,
    # same rendering, only the model changes.
    rendered = render_for_model(
        sender=sender,
        recipient=recipients,
        cc=cc,
        reply_to=reply_to,
        date=date,
        subject=message.subject or "",
        body=text,
    )
    if extractor is llm and thread_id:
        # The paid path goes thread-level: one call on the thread's latest
        # message (which quotes the whole history) classifies every member,
        # and the stored reading answers for siblings with no second call.
        payload, reused = _thread_llm_payload(
            llm,
            repos,
            thread_id=thread_id,
            rules_context=rules_context,
            fallback_prompt=prompt,
            fallback_rendered=rendered,
        )
        if reused:
            out.llm_calls -= 1
            out.thread_llm_reused += 1
    else:
        payload = extractor.extract(prompt, EXTRACTION_SCHEMA, text=rendered)
    extraction = from_payload(payload)

    if verdict.status == "positive" and known_contact and extraction.is_negative:
        # known_contact is a *proven* fact from an earlier confident
        # classification, not this extractor's guess about this one message —
        # a reply that is all "Tuesday works, see you then" trips none of
        # rulebased.py's regex signals and would otherwise be silently
        # mislabeled negative. The override promotes to "unclassified", not
        # "positive": the domain is trusted, this one message's content
        # still isn't, so it lands in review rather than skipping straight
        # to the record.
        extraction = replace(extraction, label="unclassified")

    if extraction.is_negative:
        out.not_job_related += 1
        repos.messages.mark_classified(message.id, extractor.name)
        repos.reviews.resolve_by_message(message.id)
        return

    # `agency_domain` widens `names_a_company`: an agency sender that names
    # no specific client still has a real entity to resolve to (the agency
    # itself, see `_resolve_company`'s fallback) — without this, that mail
    # sits in review_queue forever with no path out, exactly the gap this
    # session's online learning is meant to close.
    ambiguous = not extraction.is_positive or not (
        extraction.names_a_company or agency_domain
    )

    # Second opinion from a bigger model, only when the first one was
    # genuinely unsure — this is the expensive path, spent only on the
    # messages that need it. A model that punts twice stays punted; this
    # is a second read, not a tiebreak vote, so its answer (including a
    # second "unclassified") replaces the first one outright.
    if ambiguous and escalation_llm is not None:
        escalated_payload = escalation_llm.extract(
            prompt, EXTRACTION_SCHEMA, text=rendered
        )
        extraction = from_payload(escalated_payload)
        payload = escalated_payload
        extractor = escalation_llm
        out.escalated += 1
        if extraction.is_negative:
            out.not_job_related += 1
            repos.messages.mark_classified(message.id, extractor.name)
            repos.reviews.resolve_by_message(message.id)
            return
        ambiguous = not extraction.is_positive or not (
            extraction.names_a_company or agency_domain
        )

    if ambiguous:
        reason = (
            "unclassified"
            if not extraction.is_positive
            else "job-related but no company identified"
        )
        repos.reviews.enqueue(
            message.id,
            extraction=payload,
            reason=reason,
            extracted_by=extractor.name,
        )
        out.queued_for_review += 1
        repos.messages.mark_classified(message.id, extractor.name)
        return

    _record(
        message,
        extraction,
        repos=repos,
        taught_domains=taught_domains,
        agency_domain=agency_domain,
        out=out,
    )

    if embed:
        vector = llm.embed([text[:2000]])[0]
        repos.messages.set_embedding(message.id, vector)

    out.recorded += 1
    repos.messages.mark_classified(message.id, extractor.name)
    repos.reviews.resolve_by_message(message.id)


def _record(
    message: Message,
    extraction: Extraction,
    *,
    repos: Repositories,
    taught_domains: set[str],
    agency_domain: str | None,
    out: ClassifyResult,
) -> None:
    """Write a `label="positive"` extraction into the record.

    Also closes the online-learning loop: this extraction already earned
    "positive" — the same bar trusted to create the company/application/stage
    event below — so its `company_domain` is trusted enough to become a rule
    too — the next message from the same domain shouldn't need an extractor
    call to learn what this one already confirmed. Taught `undecided`, not
    `positive`: a system-inferred rule from one message hasn't earned the
    zero-content, direct-record bypass a human curating a batch grants
    (see this session's manual `positive` rules) — it only needs to escape
    the generic bulk-header/bank/label *negative* tier, same mechanism as
    the amazon.com/bigbox.com-style ambiguous domains. A human can promote
    it to `positive` later via `learn` once enough messages confirm it's
    safe to skip the extractor entirely.
    """
    assert message.id is not None
    if extraction.stage in ("offer", "accepted", "declined") and (
        extraction.company_kind == "agency"
        or (agency_domain and not extraction.names_a_company)
    ):
        # The record target is the agency itself, not a client employer —
        # an "offer"/"accepted"/"declined" here is about the *pitch*
        # ("thanks, I'll pass" / "sure, let's talk"), not employment.
        # Recording it minted phantom offers on agency rows (live-caught:
        # three agencies counted as declined offers on the dashboard).
        # When an agency conveys a real client offer, the client is named,
        # company_kind is "employer", and this guard never fires.
        extraction = replace(extraction, stage=None)
    company = _resolve_company(
        extraction, message.sent_at, repos, out, agency_domain=agency_domain
    )
    assert company.id is not None

    if agency_domain:
        normalised_agency = normalise_domain(agency_domain)
        # If the primary company already *is* the agency (the no-client
        # fallback in `_resolve_company` fired), there is nothing secondary
        # to record — linking it to itself would be a redundant, self-
        # referential row.
        if normalised_agency != (company.domain or ""):
            agency = _resolve_agency(normalised_agency, message.sent_at, repos, out)
            assert agency.id is not None
            repos.message_companies.link(message.id, agency.id, role="agency")

    raw_domain = extraction.company_domain
    domain = normalise_domain(raw_domain) if raw_domain else None
    if domain and domain not in taught_domains and not _is_hardcoded_domain(domain):
        repos.sender_rules.add(
            SenderRule(
                match_type="domain", value=domain, verdict="undecided",
                company_id=company.id, source="auto",
            )
        )
        taught_domains.add(domain)
        out.rules_learned += 1
        _journal_entry(
            domain=domain,
            company=company,
            stage=extraction.stage,
            subject=message.subject,
        )

    application = _resolve_application(
        extraction, message.sent_at, company.id, repos, out
    )
    contact = _resolve_contact(extraction, repos)

    repos.messages.link(
        message.id,
        company_id=company.id,
        application_id=application.id if application else None,
        contact_id=contact.id if contact else None,
    )

    # Backward thread propagation: siblings that were classified before this
    # thread had a company stay stranded otherwise — carry-forward only helps
    # messages that arrive AFTER the thread settles.
    if message.thread_id and hasattr(repos.messages, "link_thread_siblings"):
        repos.messages.link_thread_siblings(
            message.thread_id,
            company_id=company.id,
            application_id=application.id if application else None,
        )

    if contact is not None and contact.id is not None:
        repos.contacts.link_company(
            contact.id,
            company.id,
            role_title=None,
            first_seen_at=message.sent_at,
            last_seen_at=message.sent_at,
        )

    if extraction.stage and application is not None and application.id is not None:
        # Evidence-linked by construction: the message being classified *is* the
        # evidence, so there is no path here that records a claim without one.
        repos.stages.add(
            StageEvent(
                application_id=application.id,
                stage=extraction.stage,
                occurred_at=extraction.occurred_at or message.sent_at,
                evidence_message_id=message.id,
                # No numeric confidence to carry any more (label-based
                # routing) — defaults to None, same nullable column
                # historical rows' real values still live in.
                extracted_by="llm",
            )
        )
        out.stage_events += 1

        if extraction.stage in {"rejected", "withdrawn", "accepted", "declined"}:
            ended_at = extraction.occurred_at or message.sent_at
            # `pick_application`'s window has a grace period before
            # `started_at` (see resolve.py's `ApplicationWindow.contains`),
            # so a message can legitimately match an application it
            # predates. Closing with that earlier date would violate the DB's
            # `application_ended_after_start` check — this is a mismatched
            # window, not a real end date, so the stage event above still
            # records the evidence but the application itself is left open
            # rather than crashing the batch on a fabricated interval.
            if ended_at >= application.started_at:
                repos.applications.close(
                    application.id, outcome=extraction.stage, ended_at=ended_at
                )


def _resolve_company(
    extraction: Extraction,
    when: datetime,
    repos: Repositories,
    out: ClassifyResult,
    *,
    agency_domain: str | None = None,
) -> Company:
    """Find or create the company. Domain first — it is the strongest signal.

    ``agency_domain`` is the no-client fallback: when the sender matched a
    RECRUITING_AGENCY rule and the extraction named neither a company nor a
    domain of its own (the common case — a lot of agency mail never names
    the client at all), the agency itself becomes the entity this message
    resolves to (``kind="agency"``) rather than a placeholder "Unknown"
    company or an indefinite stay in `review_queue`. A real named employer
    still wins whenever one is present — this is strictly a last resort.
    """
    raw_domain = extraction.company_domain
    domain = normalise_domain(raw_domain) if raw_domain else None
    # A GENERIC_DOMAINS domain (personal webmail, an ATS, a scheduler...) is
    # never a real employer's identity — `prefilter.score` already refuses
    # to treat one as a positive signal, but nothing stopped it becoming the
    # company itself once it reached here. Real bug, live-caught: the
    # extractor read a recruiter's personal `@gmail.com` reply address as
    # `company_domain`, and 13 unrelated real employers (PayPal, Amazon,
    # several real employers among them) all got folded into one bogus "Gmail"
    # company across 26 messages, each spawning its own duplicate
    # application instead of reaching the real one. Treated as no domain at
    # all here — falls through to name-based alias resolution below, same as
    # any other message where the extractor found no domain.
    if domain in prefilter.GENERIC_DOMAINS:
        domain = None
    kind: CompanyKind = "employer"
    if domain is None and not extraction.company_name and agency_domain:
        domain = agency_domain
        kind = "agency"
    # The model's own read of the message beats both defaults above — it
    # saw content a sender-domain rule never does (e.g. "on behalf of our
    # client", or a brand-new agency domain nobody has taught a rule for
    # yet).
    if extraction.company_kind in ("employer", "agency"):
        kind = extraction.company_kind  # type: ignore[assignment]
    if domain:
        found = repos.companies.by_domain(domain)
        if found is not None:
            repos.companies.widen(found.id, when)
            return found

    for alias in _alias_forms(extraction.company_name, domain):
        found = repos.companies.by_alias(alias)
        if found is not None:
            repos.companies.widen(found.id, when)
            return found

    fallback = domain.split(".")[0].capitalize() if domain else "Unknown"
    # A name this short is more likely a truncation/hallucination than a
    # real company — live-caught: a LinkedIn recruiter InMail pitching an
    # unnamed "YC-Backed AI Startup", no domain at all, became a company
    # literally named "Go". A real short name (GE, 3M) almost always arrives
    # with its own domain too, which already returned above via
    # `by_domain` — this floor only ever applies to the domain-less,
    # name-only creation path, where nothing else corroborates it.
    plausible_name = extraction.company_name and len(extraction.company_name.strip()) >= 3
    name = extraction.company_name if plausible_name else fallback
    created = repos.companies.add(
        Company(
            canonical_name=name,
            domain=domain,
            kind=kind,
            first_seen_at=when,
            last_seen_at=when,
        )
    )
    assert created.id is not None
    # Aliases stored normalised so `by_alias` is a lookup, not a scan. The
    # canonical name keeps its real capitalisation on the company row.
    for alias in _alias_forms(name, domain):
        repos.companies.add_alias(
            CompanyAlias(company_id=created.id, alias=alias, source="llm")
        )
    out.companies_created += 1
    return created


def _resolve_agency(
    domain: str, when: datetime, repos: Repositories, out: ClassifyResult
) -> Company:
    """Find or create the agency's own company row — the entity a
    RECRUITING_AGENCY sender resolves to as a *secondary* link when a
    distinct employer was also named (see `_record`). Domain-only: an
    agency's own name rarely needs `_resolve_company`'s alias-collision
    handling the way a real employer's does.
    """
    found = repos.companies.by_domain(domain)
    if found is not None:
        repos.companies.widen(found.id, when)
        return found
    created = repos.companies.add(
        Company(
            canonical_name=domain.split(".")[0].capitalize(),
            domain=domain,
            kind="agency",
            first_seen_at=when,
            last_seen_at=when,
        )
    )
    assert created.id is not None
    out.companies_created += 1
    return created


#: `docs/learned-rules.md`, resolved relative to this file rather than the
#: process's working directory — this module can run as `python -m
#: jobd.cli.main` from anywhere, but the journal lives at a fixed place in
#: the checkout, same as `migrator.py`'s `MIGRATIONS_DIR` pattern.
_JOURNAL_PATH = Path(__file__).resolve().parents[3] / "docs" / "learned-rules.md"

_JOURNAL_HEADER = (
    "# Learned Rules — Online Learning Journal\n\n"
    "Auto-generated by `_record()`'s online learning (see `classify.py`). "
    "Each entry is a domain/company decision with the reasoning that led "
    "there — consulted as few-shot context when classifying an *uncertain* "
    "message from a similar domain, never as a substitute for an exact "
    "`sender_rule` match.\n"
)


def _journal_entry(
    *,
    domain: str,
    company: Company,
    stage: str | None,
    subject: str | None,
) -> None:
    """Append one reasoning entry to the online-learning journal, grouped
    under the matching section so a relevant slice can be read back later
    (`learned_rules_context`) without embeddings — just a section lookup by
    kind. Plain file append gated by the same `taught_domains` check as the
    DB write in `_record`, so this fires once per domain, not per message —
    keeping it off the pipeline's hot path.
    """
    section = "RECRUITING_AGENCY" if company.kind == "agency" else "Direct employers"
    existing = _JOURNAL_PATH.read_text() if _JOURNAL_PATH.exists() else _JOURNAL_HEADER
    heading = f"\n## {section}\n"
    if heading not in existing:
        existing = existing.rstrip("\n") + "\n" + heading

    subject_snippet = f' from "{subject}"' if subject else ""
    entry = (
        f"\n### {domain}\n"
        f"- Kind: {company.kind} · Company: {company.canonical_name}\n"
        f"- {stage or 'no stage'}{subject_snippet}\n"
    )
    if f"### {domain}\n" in existing:
        return  # already journaled this domain — taught_domains should have
        # caught this already, this is just the same idempotency belt-and-
        # braces as SenderRuleRepository.add's ON CONFLICT.

    idx = existing.index(heading) + len(heading)
    updated = existing[:idx] + entry + existing[idx:]
    _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _JOURNAL_PATH.write_text(updated)


def learned_rules_context(section: str, *, limit: int = 8) -> str:
    """A bounded slice of `docs/learned-rules.md` — the entries under one
    section, for use as few-shot prompt context when an uncertain message
    falls in the same category. Empty string when there's no matching
    section, rather than falling back to unrelated entries: irrelevant
    context costs tokens on every uncertain call without earning anything
    back (see `_one`'s "keep the pipeline fast" constraint). No embeddings,
    no ranking — a section lookup by name is the entire relevance mechanism
    for v1, matching how `_journal_entry` groups entries in the first place.
    """
    if not _JOURNAL_PATH.exists():
        return ""
    text = _JOURNAL_PATH.read_text()
    heading = f"\n## {section}\n"
    if heading not in text:
        return ""
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = rest.find("\n## ")
    body = (rest[:end] if end != -1 else rest).strip("\n")
    if not body:
        return ""
    entries = [f"### {e}" for e in body.split("### ") if e.strip()][:limit]
    if not entries:
        return ""
    return "Prior confirmed examples in this category:\n" + "\n".join(entries)


def _alias_forms(name: str | None, domain: str | None) -> list[str]:
    """Every normalised spelling one company answers to.

    Both spaced and squashed — "acme robotics" *and* "acmerobotics" — because
    the same company arrives spelled two ways: written out in a message body,
    and squashed into a domain. Without the squashed form, an ATS mail naming
    "Acme Robotics" and a recruiter mailing from `acmerobotics.test` become two
    company pages that never join.

    Plus the domain's own label, which is what a rules-based extractor
    reconstructs a name from when the body names nobody.
    """
    forms: list[str] = []
    for candidate in (name, domain.split(".")[0] if domain else None):
        if not candidate:
            continue
        spaced = normalise_company(candidate)
        if not spaced:
            continue
        forms.append(spaced)
        squashed = spaced.replace(" ", "")
        if squashed != spaced:
            forms.append(squashed)
    # Order-preserving dedupe: the first form is the one a human would read.
    return list(dict.fromkeys(forms))


def _resolve_application(
    extraction: Extraction,
    when: datetime,
    company_id: UUID,
    repos: Repositories,
    out: ClassifyResult,
) -> Application | None:
    """Find the application this message belongs to, or open a new one."""
    existing = repos.applications.for_company(company_id)
    windows = [
        ApplicationWindow(
            id=a.id,
            role_title=a.role_title,
            started_at=a.started_at,
            ended_at=a.ended_at,
        )
        for a in existing
        if a.id is not None
    ]
    chosen = pick_application(
        windows, occurred_at=when, role_title=extraction.role_title
    )
    if chosen is not None:
        # ApplicationWindow.id is `object` so the domain rules stay free of
        # database identifiers. The service is where it is safe to narrow.
        by_id = {a.id: a for a in existing}
        matched = by_id[cast(UUID, chosen.id)]
        if matched.role_title is None and extraction.role_title:
            repos.applications.set_role(matched.id, extraction.role_title)
        return matched

    created = repos.applications.add(
        Application(
            company_id=company_id,
            role_title=extraction.role_title,
            started_at=when,
            source="inferred",
        )
    )
    out.applications_created += 1
    return created


#: Automated-system local-parts. Never worth a Contact row: nobody is ever
#: personally recruiting from `noreply@`, and a Contact created here becomes
#: a `known_contact` — a signal `prefilter.score` treats as *proof* the
#: correspondent is job-related, strong enough to override even a
#: human-taught negative domain rule. One false extraction (a marketing
#: email misread as job-related) is enough to poison it permanently, and it
#: silently defeats every negative rule taught for that domain afterward —
#: found via `noreply@robinhood.com`, which had linked 428 pure account/
#: marketing messages back into the review queue despite robinhood.com
#: already being in BANK_DOMAINS. Narrow on purpose: real ATS/company
#: addresses like `careers@` or `hello@` do sometimes represent a genuine
#: recruiting channel, so only the unambiguous automated markers are here.
_AUTOMATED_LOCAL_PARTS = frozenset(
    {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"}
)


def _resolve_contact(extraction: Extraction, repos: Repositories) -> Contact | None:
    """Find or create the person, keyed on their address.

    Cross-channel merging happens here by construction: the identity table is
    unique on (channel, address), so the same recruiter writing from a second
    address becomes a second identity on the same contact only once a human
    links them. Guessing that two addresses are one person is exactly the merge
    this module refuses to make on its own.
    """
    if not extraction.contact_email:
        return None
    local_part = extraction.contact_email.split("@", 1)[0].lower()
    if local_part in _AUTOMATED_LOCAL_PARTS:
        return None
    found = repos.contacts.by_identity("email", extraction.contact_email)
    if found is not None:
        return found
    created = repos.contacts.add(Contact(display_name=extraction.contact_name))
    assert created.id is not None
    repos.contacts.add_identity(
        ContactIdentity(
            contact_id=created.id, channel="email", identifier=extraction.contact_email
        )
    )
    return created


def _known_contact(
    sender: str, recipients: str, account: str, repos: Repositories
) -> bool:
    """Whether either correspondent is already a contact in the record.

    The signal that rescues content-free replies in an established thread. The
    user's own address is excluded: it appears on every message, so counting it
    would mark the entire mailbox as a candidate.
    """
    from email.utils import getaddresses

    for _, address in getaddresses([sender, recipients]):
        lowered = address.lower()
        if not lowered or lowered == account.lower():
            continue
        if repos.contacts.by_identity("email", lowered) is not None:
            return True
    return False


def _is_hardcoded_domain(domain: str) -> bool:
    """Whether a domain is already decided by a hardcoded list — `_record`'s
    auto-teach must never write a `sender_rule` for one of these.

    `BANK_DOMAINS` is the concrete case that surfaced this: a stale company
    record already exists with `domain="robinhood.com"` from before today's
    guards (`_company_domain` in rulebased.py only ever excluded
    `GENERIC_DOMAINS`, not `BANK_DOMAINS`, so a bank sender that slipped
    past the prefilter once could still get its domain read as a company).
    Auto-teaching `undecided` for a bank domain would be actively harmful,
    not just redundant: `undecided` outranks `bank_hit` in `prefilter.score`
    (that is the whole point of the tier — it exists to *protect* a sender
    from the bulk-header/bank/label negative check), so it would silently
    reopen every future message from that domain to the extractor instead
    of leaving it correctly, permanently negative.
    """
    return any(
        domain == d or domain.endswith(f".{d}")
        for d in (prefilter.BANK_DOMAINS | prefilter.GENERIC_DOMAINS)
    )


def _match_rule(
    rules: list[SenderRule], sender: str, recipients: str
) -> tuple[prefilter.LearnedVerdict | None, UUID | None, str | None, str | None]:
    """The fanout lookup: does any learned rule cover this sender or any
    recipient? Checked by address first (the more specific match), then
    domain — an address-level correction ("this one person, not the whole
    domain") should not be shadowed by a coarser domain rule.

    Domain rules match by suffix (`shein.com` covers `market-us.shein.com`
    *and* `news.edmmarket.shein.com`), same as `ATS_DOMAINS`/`BANK_DOMAINS` —
    real marketing platforms send from a different subdomain per campaign,
    and an exact-match rule would need re-teaching for every one of them.

    Returns ``(verdict, company_id, category, matched_domain)``. The 4th
    element is the matched rule's own domain value (lowercased) when the
    match was a domain rule, else `None` — `_one`'s RECRUITING_AGENCY
    handling needs to know *which* domain matched, not just that one did.
    """
    from email.utils import getaddresses

    from jobd.domain.prefilter import domain_of

    addresses = [a.lower() for _, a in getaddresses([sender, recipients]) if a]
    domains = {domain_of(a) for a in addresses}

    by_address = {r.value.lower(): r for r in rules if r.match_type == "address"}
    domain_rules = [r for r in rules if r.match_type == "domain"]

    for address in addresses:
        rule = by_address.get(address)
        if rule:
            return rule.verdict, rule.company_id, rule.category, None
    for domain in domains:
        rule = next(
            (
                r
                for r in domain_rules
                if domain == r.value.lower() or domain.endswith(f".{r.value.lower()}")
            ),
            None,
        )
        if rule:
            return rule.verdict, rule.company_id, rule.category, rule.value.lower()
    return None, None, None, None


def _header(payload: bytes, name: str) -> str:
    """One header, or empty. Used only for pre-filter signals."""
    from email import message_from_bytes, policy

    try:
        parsed = message_from_bytes(payload, policy=policy.default)
        return str(parsed.get(name) or "")
    except Exception:
        return ""


def _is_bulk(payload: bytes) -> bool:
    """RFC-standard bulk-mail markers: a `List-Unsubscribe` header, or
    `Precedence: bulk`/`Precedence: list`. Structural, so it catches mailing
    platforms (Substack, Mailchimp, HubSpot...) no phrase list would name."""
    if _header(payload, "list-unsubscribe"):
        return True
    return _header(payload, "precedence").strip().lower() in {"bulk", "list"}


@dataclass
class SweepResult:
    """What `sweep_review_queue` did — mirrors ClassifyResult's vocabulary
    so the CLI can print both the same way."""

    seen: int = 0
    not_job_related: int = 0
    recorded: int = 0
    still_queued: int = 0
    #: Resolved with zero model calls: the message's thread (Gmail threadId,
    #: or the RFC In-Reply-To chain) already named the company.
    thread_carried: int = 0
    #: Resolved with zero model calls by the metadata prefilter, which can
    #: finally run DB-only now that labels and the bulk marker are columns.
    prefiltered: int = 0
    #: Non-bulk mail from a known company's own domain, linked directly —
    #: the record's own entities as a deterministic gate.
    domain_carried: int = 0
    #: Job-related with no nameable employer, twice, independently — an
    #: anonymized pitch. Closed without a record row; the mail remains.
    no_entity: int = 0
    #: Resolved from a thread's stored model reading — zero judge calls.
    thread_reused: int = 0
    rules_learned: int = 0
    errors: list[str] = field(default_factory=list)
    #: Sender domains of the messages the judge rejected, with counts — a
    #: domain rejected repeatedly is a `jobd learn ... negative` candidate,
    #: surfaced for a human to confirm rather than auto-taught: the sweep's
    #: whole premise is that these messages already fooled one model.
    rejected_domains: dict[str, int] = field(default_factory=dict)


def sweep_review_queue(
    *,
    judge: LLMProvider,
    repos: Repositories,
    conn: psycopg.Connection[Any],
    limit: int = 500,
    judge_workers: int = 8,
    fresh: bool = False,
    meter: Any = None,
) -> SweepResult:
    """Re-judge every pending review-queue item with a stronger model.

    The queue holds exactly the messages the classify-time model punted on,
    so re-running the same model would reproduce the same punt. This pass
    hands each one to the judge model instead — the same second-opinion role
    `escalation_llm` plays at classify time, applied late to a queue that
    accumulated without one.

    DB-only on purpose: the judge reads the stored subject and body text,
    which is the same evidence the human reviewer would have read on the
    triage page — no raw-store fetch, so the sweep runs with the S3 session
    expired. Verdicts apply exactly as classify would have applied them:
    negative resolves the item and marks the message; positive with a named
    company records it through `_record` (companies, applications, stage
    events, learned rules — the full path); anything still ambiguous stays
    queued with the judge's extraction attached, and that residue is the
    honest size of the human's job.
    """
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    out = SweepResult()
    rules = repos.sender_rules.all()
    taught = {r.value.lower() for r in rules if r.match_type == "domain"}
    inner = ClassifyResult()

    rows = conn.execute(
        """
        SELECT rq.id, rq.message_id, rq.reason,
               m.reply_to, m.cc_addresses, m.is_bulk, m.in_reply_to,
               m.raw_metadata ->> 'labels' AS labels
        FROM review_queue rq JOIN message m ON m.id = rq.message_id
        WHERE rq.status = 'pending' ORDER BY rq.created_at LIMIT %s
        """,
        (limit,),
    ).fetchall()

    meter.set_total(len(rows))
    meter.set_counter("phase", "free-gates")
    judge_jobs: list[dict[str, Any]] = []

    for (review_id, message_id, prior_reason,
         reply_to, cc_addresses, is_bulk, in_reply_to, labels) in rows:
        message = repos.messages.get(message_id)
        if message is None or message.id is None:
            meter.bump("skipped")
            continue
        out.seen += 1
        sender = message.sender_address or ""
        text = (message.body_text or "")[:6000]

        learned, _, learned_category, learned_domain = _match_rule(
            rules, sender, ""
        )
        agency_domain = (
            learned_domain
            if learned_category == prefilter.SenderCategory.RECRUITING_AGENCY
            else None
        )

        # Free paths first, now that the envelope columns exist (0026):
        # thread-carry — this message's thread already resolved to a company
        # (via Gmail's threadId, or the RFC In-Reply-To chain when a source
        # has no native thread id) — links it with zero model calls, exactly
        # like classify's own carry branch.
        thread_ctx = (
            repos.messages.thread_context(message.thread_id)
            if message.thread_id
            else None
        )
        if thread_ctx is None and in_reply_to:
            thread_ctx = repos.messages.rfc_parent_context(in_reply_to)
        if thread_ctx is not None:
            company_id, application_id, contact_id = thread_ctx[:3]
            with conn.transaction():
                repos.messages.link(
                    message.id,
                    company_id=company_id,
                    application_id=application_id,
                    contact_id=contact_id,
                )
                repos.messages.mark_classified(message.id, "sweep:thread-carry")
                repos.reviews.resolve_by_message(message.id)
            out.thread_carried += 1
            meter.bump("thread_carry")
            continue

        # Metadata prefilter — the same gate classify runs before any model.
        verdict = prefilter.score(
            sender=sender,
            recipients="",
            learned=learned,
            gmail_labels=labels or "",
            has_list_unsubscribe=bool(is_bulk),
        )
        if verdict.status == "negative":
            with conn.transaction():
                repos.messages.mark_classified(
                    message.id, f"sweep:prefilter:{verdict.status}"
                )
                repos.reviews.resolve_by_message(message.id)
            out.prefiltered += 1
            meter.bump("prefilter_negative")
            continue

        # Company-domain carry: non-bulk mail whose sender domain IS a known
        # company's own domain (or a subdomain of it) belongs to that
        # company — "Interview with Acme!" from an @acme.com human when
        # Acme already exists in the record needs no judge. Bulk-marked
        # mail is excluded on purpose: a company's marketing rides the same
        # domain as its recruiters, and the bulk marker is what separates
        # the newsletter from the interview invite.
        if not is_bulk and "@" in sender:
            sender_domain = sender.rsplit("@", 1)[-1].lower().strip(">")
            crow = conn.execute(
                """
                SELECT id FROM company
                WHERE domain IS NOT NULL AND domain <> ''
                  AND (lower(domain) = %s OR %s LIKE '%%.' || lower(domain))
                ORDER BY length(domain) DESC LIMIT 1
                """,
                (sender_domain, sender_domain),
            ).fetchone()
            if crow:
                with conn.transaction():
                    repos.messages.link(message.id, company_id=crow[0])
                    repos.messages.mark_classified(
                        message.id, "sweep:domain-carry"
                    )
                    repos.reviews.resolve_by_message(message.id)
                out.domain_carried += 1
                meter.bump("domain_carry")
                continue

        # A thread the model already read answers from its stored row — no
        # second call for a conversation the model has seen (in any run:
        # classify, a partition worker, or an earlier sweep). `fresh` skips
        # the reuse on purpose: for a queue whose stored readings are all
        # punts, replaying the cache can only reproduce the punt, so the
        # caller pays for a new reading and the upsert replaces the row.
        stored = (
            repos.messages.thread_extraction(message.thread_id)
            if message.thread_id and not fresh
            else None
        )

        section = "RECRUITING_AGENCY" if agency_domain else "Direct employers"
        context = learned_rules_context(section)
        job: dict[str, Any] = {
            "review_id": review_id,
            "message": message,
            "prior_reason": prior_reason,
            "agency_domain": agency_domain,
            "sender": sender,
            "precomputed": None,
            "save_thread": None,
        }
        if stored is not None:
            job["precomputed"] = dict(stored["payload"])
            out.thread_reused += 1
        else:
            latest = (
                repos.messages.thread_latest(message.thread_id)
                if message.thread_id
                else None
            )
            if latest is not None:
                # Thread-level judge call: the latest message quotes the
                # whole history, so one read covers every sibling — stored
                # below so none of them ever pays again.
                job["prompt"] = (
                    (context + "\n" + THREAD_PROMPT) if context else THREAD_PROMPT
                )
                job["rendered"] = render_thread_for_model(
                    sender=latest["sender"],
                    recipient=latest["recipients"],
                    subject=latest["subject"],
                    body=latest["body"][:_THREAD_BODY_CAP],
                    cc=latest["cc"],
                    reply_to=latest["reply_to"],
                    date=str(latest["sent_at"]) if latest["sent_at"] else None,
                    labels=latest["labels"],
                    thread_messages=latest["thread_messages"],
                    thread_senders=latest["thread_senders"],
                    first_date=latest["first_date"],
                )
                job["schema"] = THREAD_EXTRACTION_SCHEMA
                job["save_thread"] = {
                    "thread_id": message.thread_id,
                    "latest_message_id": latest["id"],
                    "covers_sent_at": latest["sent_at"],
                    "messages_covered": latest["thread_messages"],
                }
            else:
                job["prompt"] = (context + "\n" + PROMPT) if context else PROMPT
                job["rendered"] = render_for_model(
                    sender=sender,
                    recipient="",
                    subject=message.subject or "",
                    body=text,
                    cc=", ".join(cc_addresses) if cc_addresses else None,
                    reply_to=reply_to,
                    date=str(message.sent_at) if message.sent_at else None,
                    labels=labels,
                )
                job["schema"] = EXTRACTION_SCHEMA
        judge_jobs.append(job)
        if out.seen % 50 == 0:
            conn.commit()
    conn.commit()

    # Phase two: the judge calls, concurrently — network-bound, so a thread
    # pool multiplies throughput the way the holdout's did. DB writes stay
    # on this thread; the pool only ever touches the HTTP client. Provider
    # token counters may drift slightly under concurrency; the verdicts
    # don't.
    from concurrent.futures import ThreadPoolExecutor

    def _ask(job: dict[str, Any]) -> Any:
        if job["precomputed"] is not None:
            meter.bump("thread_reused")
            return job["precomputed"]
        try:
            payload = judge.extract(
                job["prompt"], job["schema"], text=job["rendered"]
            )
        except Exception as exc:  # noqa: BLE001 — scored as an error below
            meter.bump("judged")
            return exc
        # The pool thread bumps as each verdict lands, so the live row moves
        # during the minutes-long judge phase, not only at apply time.
        meter.bump("judged")
        return payload

    meter.set_counter("phase", "judge")
    meter.set_counter("judge_jobs", len(judge_jobs))

    with ThreadPoolExecutor(max_workers=judge_workers) as pool:
        payloads = list(pool.map(_ask, judge_jobs))

    meter.set_counter("phase", "apply")
    for job, payload in zip(judge_jobs, payloads):
        message = job["message"]
        if isinstance(payload, Exception):
            out.errors.append(f"{message.id}: {payload}")
            meter.error(f"{message.id}: {payload}")
            continue
        if job["save_thread"] is not None:
            # Cache the reading — punts included: a sibling of a punted
            # thread queues from the row instead of paying to punt again.
            st = job["save_thread"]
            repos.messages.save_thread_extraction(
                st["thread_id"],
                latest_message_id=st["latest_message_id"],
                covers_sent_at=st["covers_sent_at"],
                model=judge.name,
                payload=payload,
                messages_covered=st["messages_covered"],
                reasoning=getattr(judge, "last_reasoning", None),
            )
        agency_domain = job["agency_domain"]
        sender = job["sender"]
        prior_reason = job["prior_reason"]
        try:
            extraction = from_payload(payload)
            with conn.transaction():
                if extraction.is_negative:
                    out.not_job_related += 1
                    if "@" in sender:
                        domain = sender.rsplit("@", 1)[-1].lower()
                        out.rejected_domains[domain] = (
                            out.rejected_domains.get(domain, 0) + 1
                        )
                    repos.messages.mark_classified(message.id, judge.name)
                    repos.reviews.resolve_by_message(message.id)
                elif extraction.is_positive and (
                    extraction.names_a_company or agency_domain
                ):
                    _record(
                        message,
                        extraction,
                        repos=repos,
                        taught_domains=taught,
                        agency_domain=agency_domain,
                        out=inner,
                    )
                    out.recorded += 1
                    repos.messages.mark_classified(message.id, judge.name)
                    repos.reviews.resolve_by_message(message.id)
                else:
                    reason = (
                        "unclassified"
                        if not extraction.is_positive
                        else "job-related but no company identified"
                    )
                    # Two independent passes both landed "job-related, but
                    # nothing nameable" — an anonymized pitch. A human can't
                    # extract a company that isn't in the mail either; a
                    # second concurring opinion settles it.
                    no_company = "job-related but no company identified"
                    if reason == no_company and prior_reason == no_company:
                        repos.reviews.resolve(job["review_id"], "approved")
                        repos.messages.mark_classified(message.id, judge.name)
                        out.no_entity += 1
                    else:
                        repos.reviews.enqueue(
                            message.id,
                            extraction=payload,
                            reason=reason,
                            extracted_by=judge.name,
                        )
                        out.still_queued += 1
        except Exception as exc:  # one bad message costs itself, not the run
            out.errors.append(f"{message.id}: {exc}")
            meter.error(f"{message.id}: {exc}")
        conn.commit()

    conn.commit()
    out.rules_learned = inner.rules_learned
    for key, value in (
        ("judge_negative", out.not_job_related),
        ("judge_recorded", out.recorded),
        ("no_entity", out.no_entity),
        ("still_queued", out.still_queued),
        ("rules_learned", out.rules_learned),
        ("thread_reused", out.thread_reused),
    ):
        meter.set_counter(key, value)
    meter.flush(force=True)
    return out
