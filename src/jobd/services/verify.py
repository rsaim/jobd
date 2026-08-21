"""Verification: an independent, whole-chain audit pass over companies
already produced by `classify_pending` (PRD's own "check the pipeline's
work" ask — deliberately separate from the hot classify path).

`classify_pending` decides one message at a time, cheaply: the pre-filter
rules do most of the work, and an extractor call — when one happens at all —
reads exactly one message's text. This module asks a different, more
expensive question, once per *company* rather than per message: given
everything the pipeline has linked to this entity so far, is it actually job
related? What is this entity really called — employer or agency? And what
deterministic rule, if any, would a human confirm having read the whole
chain, that no single message would have justified on its own?

Findings are stored (`company_verification`) always; whether they are also
*applied* to the record is the caller's call (`apply=True`). Unlike
`classify.py._record`'s online learning — which never auto-teaches `negative`
because a wrong blanket-negative guess this session cost real employer
domains (robinhood.com, instacart.com — see that module's
docstring) — this pass has read a company's *entire* chain, not one message,
so its verdict is a stronger claim than a single extraction's guess.
`apply=True` writes every suggested sender_rule, including `negative`, tagged
`source="auto"`.

**Not job related → the company is removed, not just flagged.** The
labelling always lives at the email level (`message.classified_by`), never
only at the company's: when the model marks `classification_correct=False`,
every message linked to that company (primary or secondary) gets a direct,
final negative label — `messages.mark_company_negative`, no unclassified
limbo, no later resweep needed, because this pass already read every one of
those messages to reach its verdict — and the company row itself is then
deleted (`companies.delete`). Cascades take its applications/aliases/links
with it; `company_verification`'s own row survives regardless (migration
0016), because the audit trail that justified a deletion needs to outlive
what it audited. `apply=True` also corrects a company's `canonical_name`/
`kind` when the model's read of the whole chain disagrees with what a single
earlier extraction guessed — but only when `classification_correct` is
true, since that path is for a company worth renaming, not one worth
removing.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from jobd.domain.record import (
    Company,
    CompanyAlias,
    CompanyKind,
    CompanyVerification,
    Message,
    SenderRule,
)
from jobd.domain.verification import (
    PROMPT,
    VERIFICATION_SCHEMA,
    RuleSuggestion,
    VerificationResult,
    from_payload,
    render_thread_for_model,
)
from jobd.ports import LLMProvider

#: Bounds token cost per company. A prolific company's chain is read from its
#: most recent MAX_MESSAGES — enough to judge identity and correctness
#: without paying to read a multi-year history in full on every pass. See
#: the module docstring: this is an audit, not the record itself, so a
#: bounded recent slice is an acceptable trade a per-message extraction
#: could never make.
MAX_MESSAGES = 60


class CompanyStore(Protocol):
    def get(self, company_id: UUID) -> Company | None: ...
    def all(self) -> list[Company]: ...
    def by_alias(self, alias: str) -> Company | None: ...
    def add_alias(self, alias: CompanyAlias) -> CompanyAlias: ...
    def rename(
        self, company_id: UUID, *, canonical_name: str, kind: CompanyKind | None = None
    ) -> None: ...
    def delete(self, company_id: UUID) -> None: ...


class MessageStore(Protocol):
    def for_company_all(self, company_id: UUID) -> list[Message]: ...
    def mark_company_negative(self, company_id: UUID, by: str) -> int: ...


class SenderRuleStore(Protocol):
    def add(self, rule: SenderRule) -> SenderRule: ...


class VerificationStore(Protocol):
    def add(self, verification: CompanyVerification) -> CompanyVerification: ...
    def verified_company_ids(self) -> set[UUID]: ...


@dataclass(slots=True)
class VerifyOutcome:
    """One company's result, for the CLI to report and the dashboard to show.

    ``result``/``verification_id`` are `None` only when ``error`` is set — a
    per-company failure (the model call itself raised — a gateway timeout, a
    rate limit, an exhausted API budget) never reaches `verifications.add`,
    so there is nothing to report beyond the error. See `verify_companies`'
    docstring on why this is caught per company rather than allowed to abort
    the whole sweep.
    """

    company: Company
    result: VerificationResult | None
    verification_id: UUID | None
    #: Rule suggestions actually written this run — positive, negative, and
    #: undecided alike (see module docstring). Empty unless `apply=True`.
    applied_rules: list[RuleSuggestion] = field(default_factory=list)
    #: Messages labelled negative and unlinked by `mark_company_negative`,
    #: immediately before the company row was deleted. Zero unless the
    #: company was actually removed this run.
    messages_marked_negative: int = 0
    #: True once the company row itself has been deleted (see module
    #: docstring) — `outcome.company` still holds the in-memory snapshot
    #: from before the delete, for the CLI/report to reference.
    company_deleted: bool = False
    #: Whether canonical_name/kind were corrected this run (mutually
    #: exclusive with company_deleted — see module docstring).
    identity_updated: bool = False
    #: Set only when this company's pass raised — everything else on this
    #: outcome is then meaningless/absent. A multi-hundred-company sweep
    #: must not die on one bad gateway response, same reasoning as
    #: `classify_pending`'s per-message isolation.
    error: str | None = None


def verify_companies(
    *,
    llm: LLMProvider,
    companies: CompanyStore,
    messages: MessageStore,
    verifications: VerificationStore,
    sender_rules: SenderRuleStore,
    limit: int = 20,
    company_id: UUID | None = None,
    reverify: bool = False,
    apply: bool = False,
    after_each: Callable[[VerifyOutcome], None] | None = None,
    concurrency: int = 6,
) -> list[VerifyOutcome]:
    """Run the audit over up to `limit` companies.

    `company_id` narrows to exactly one company, ignoring `limit`/`reverify`.
    Otherwise: every company with at least one message, skipping ones
    already verified unless `reverify` is set. `companies.all()`'s own order
    (most-recently-touched first, see `CompanyRepository.all`) decides which
    `limit` companies a sweep reaches first — active companies get audited
    before dormant ones, without a separate sort column to maintain.

    A company with no messages linked yet (should not happen in practice —
    `_resolve_company`/`_resolve_agency` never create one without recording
    at least the message that caused it) is skipped rather than sent to the
    model with nothing to read.

    ``concurrency`` runs up to that many `llm.extract` calls at once via a
    thread pool — the same shape as `classify.py._prefetch`: the model call
    is a network round trip (I/O-bound, GIL-released while waiting), so a
    small pool buys real parallelism even though Python threads. Everything
    that touches the database — `verifications.add`, `sender_rules.add`,
    `companies.delete`/`rename` — stays sequential in the main thread as each
    future completes, because a psycopg connection is not thread-safe and
    nothing here is worth a connection pool for a job that runs occasionally,
    not continuously. 1 disables the pool entirely (a plain sequential loop).

    ``after_each``, called right after every company (success or error),
    exists for one reason: this function's own writes only reach the
    database when the *caller's* connection commits, and a `limit` large
    enough for a real sweep can take long enough that killing the process —
    a timeout, a Ctrl-C, an out-of-credits crash — loses every company
    processed so far if nothing commits until the very end. A caller that
    passes `lambda o: conn.commit()` here gets a commit per company instead
    — the real, non-optional reason this hook exists, not merely a
    progress-reporting nicety (though `cli.main`'s `verify` command uses it
    for that too, streaming each result instead of holding the whole sweep
    silent until it returns). Order is completion order, not `targets`
    order, once concurrency > 1 — whichever model call answers first is
    reported first.
    """
    targets: list[Company]
    if company_id is not None:
        found = companies.get(company_id)
        targets = [found] if found is not None else []
    else:
        already = set() if reverify else verifications.verified_company_ids()
        targets = [
            c for c in companies.all() if c.id is not None and c.id not in already
        ][:limit]

    # Sequential: cheap local DB reads, not what concurrency is buying here.
    prepared: list[tuple[Company, list[Message], str]] = []
    for company in targets:
        assert company.id is not None
        thread = messages.for_company_all(company.id)
        if not thread:
            continue
        used = thread[-MAX_MESSAGES:]
        text = render_thread_for_model(
            company_name=company.canonical_name,
            company_domain=company.domain,
            messages=used,
        )
        prepared.append((company, used, text))

    outcomes: list[VerifyOutcome] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {
            pool.submit(llm.extract, PROMPT, VERIFICATION_SCHEMA, text=text): (
                company,
                used,
            )
            for company, used, text in prepared
        }
        for future in as_completed(futures):
            company, used = futures[future]
            assert company.id is not None
            try:
                payload = future.result()
                outcome = _finish_one(
                    company,
                    used,
                    payload,
                    llm_name=llm.name,
                    companies=companies,
                    messages=messages,
                    sender_rules=sender_rules,
                    verifications=verifications,
                    apply=apply,
                )
            except Exception as exc:  # one bad company must not cost the sweep
                outcome = VerifyOutcome(
                    company=company,
                    result=None,
                    verification_id=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            outcomes.append(outcome)
            if after_each is not None:
                after_each(outcome)
    return outcomes


def _finish_one(
    company: Company,
    used: list[Message],
    payload: dict[str, Any],
    *,
    llm_name: str,
    companies: CompanyStore,
    messages: MessageStore,
    sender_rules: SenderRuleStore,
    verifications: VerificationStore,
    apply: bool,
) -> VerifyOutcome:
    """Parse one already-fetched model response and write its findings —
    the sequential, DB-touching half of one company's audit pass. Factored
    out of `verify_companies` so the concurrent (network) and sequential
    (database) halves are two plainly separate functions, not interleaved
    in one that would be harder to reason about thread-safety in."""
    assert company.id is not None
    result = from_payload(payload)

    record = verifications.add(
        CompanyVerification(
            company_id=company.id,
            model=llm_name,
            message_count=len(used),
            raw_response=payload,
            classification_correct=result.classification_correct,
            verified_name=result.company_name,
            verified_kind=result.kind,
            verified_domain=result.company_domain,
            reasoning=result.reasoning,
            suggested_rules=[
                {
                    "match_type": r.match_type,
                    "value": r.value,
                    "verdict": r.verdict,
                    "reason": r.reason,
                }
                for r in result.rules
            ],
        )
    )

    applied: list[RuleSuggestion] = []
    marked_negative = 0
    company_deleted = False
    identity_updated = False
    if apply:
        # The account's own address rides on every message, so a rule about
        # it (or its domain) would decide the entire mailbox in one write —
        # live-caught: a model-suggested `the-user's-own@gmail.com -> undecided`
        # rule silently bypassed the prefilter for every inbound mail and
        # routed 92% of a full reclassify to the paid extractor. The web
        # teach form already refuses this; the sweep must too.
        own_address = (used[0].account or "").lower() if used else ""
        own_domain = own_address.rsplit("@", 1)[-1] if "@" in own_address else ""
        for rule in result.rules:
            value = (rule.value or "").lower()
            if value and value in (own_address, own_domain):
                continue
            sender_rules.add(
                SenderRule(
                    match_type=rule.match_type,
                    value=rule.value,
                    verdict=rule.verdict,
                    company_id=company.id,
                    source="auto",
                )
            )
            applied.append(rule)

        if not result.classification_correct:
            # Not job related: label every message it touched, at the
            # email level, then remove the company itself — see module
            # docstring. Rules are written first (just above), while
            # company.id still resolves, though neither a negative nor
            # an undecided rule actually needs it (SET NULL on delete).
            marked_negative = messages.mark_company_negative(
                company.id, by=f"verify:negative:{llm_name}"
            )
            companies.delete(company.id)
            company_deleted = True
        else:
            # Identity correction: only when the chain itself checks
            # out — a company worth renaming, not one worth removing.
            name_differs = bool(
                result.company_name
            ) and result.company_name != company.canonical_name
            kind_differs = result.kind != company.kind
            if name_differs or kind_differs:
                if name_differs and companies.by_alias(
                    company.canonical_name.lower()
                ) is None:
                    # Preserve the old name as an alias so anything that
                    # still remembers it (a stale link, a human search)
                    # keeps resolving — same mechanism `_resolve_company`
                    # uses when it first names a company.
                    companies.add_alias(
                        CompanyAlias(
                            company_id=company.id,
                            alias=company.canonical_name.lower(),
                            source="llm",
                        )
                    )
                companies.rename(
                    company.id,
                    canonical_name=result.company_name or company.canonical_name,
                    kind=result.kind if kind_differs else None,
                )
                if name_differs:
                    # The other half of the old-name preservation above:
                    # the *corrected* name has to become findable too, or
                    # `_resolve_company`'s `by_alias` lookup (which only
                    # ever checks `company_alias`, never `canonical_name`
                    # directly) can't find this row the next time a message
                    # correctly names it — every such message spawns a
                    # fresh duplicate company instead of reusing this one,
                    # which then gets audited and renamed too, forever.
                    # Real bug, live-caught: one real employer had
                    # accumulated 9 separate company rows this way, each
                    # correctly renamed by an earlier audit pass and each
                    # still unfindable by name for the next one.
                    companies.ensure_alias(
                        company.id, (result.company_name or "").lower(), source="llm"
                    )
                identity_updated = True

    return VerifyOutcome(
        company=company,
        result=result,
        verification_id=record.id,
        applied_rules=applied,
        messages_marked_negative=marked_negative,
        company_deleted=company_deleted,
        identity_updated=identity_updated,
    )
