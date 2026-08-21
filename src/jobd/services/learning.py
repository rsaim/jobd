"""Teaching the pipeline one rule, and fanning it out over the backlog.

This was the body of `cli/main.py:learn` and nothing about the mechanism has
changed — it moved because the dashboard needs to call it and cannot call a
Click command. Same rule `services/dashboard.py` follows for its reads: one
implementation, several surfaces, so the web and the CLI cannot drift into
two subtly different definitions of what "learn a rule" means.

The two-part mechanism, unchanged from `classify.py`'s module docstring:
`sender_rule` (so every message ingested from now on inherits the verdict for
free via `prefilter.score`'s `learned`), plus a bulk UPDATE over every
still-unclassified message that already carries the attribute (so the backlog
benefits now rather than on some future re-ingest). `undecided` skips the
second half: there is nothing to write into the record, it only ever routes
onward to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg

from jobd.domain.record import Company, SenderRule

Verdict = Literal["positive", "negative", "undecided"]


class LearnError(ValueError):
    """A rule that cannot be taught as asked. Carries a human-readable reason:
    the CLI renders it as a ClickException, the dashboard as a form error."""


@dataclass(frozen=True, slots=True)
class LearnResult:
    """What teaching one rule did."""

    match_type: Literal["domain", "address"]
    value: str
    verdict: Verdict
    company_id: UUID | None
    #: Backlog messages the fanout resolved. Always 0 for `undecided`.
    resolved: int


def _match(domain: str | None, address: str | None) -> tuple[str, str]:
    """Which attribute the stored rule is keyed on.

    Domain wins when both are given — the pre-existing CLI behaviour, kept
    deliberately on extraction. Note the asymmetry it implies: the *rule* is
    keyed on the domain while the *fanout* below still ORs in the address, so
    passing both resolves address-matched mail under a domain rule that will
    not match it again on re-ingest. Surfacing that is a separate change; this
    function only records that it is intentional here, not an oversight.
    """
    if not domain and not address:
        raise LearnError("Give a domain or an address.")
    return ("domain", domain) if domain else ("address", address)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class FanoutPreview:
    """A rule's blast radius, before anything is written.

    Two numbers because they answer two different questions, and after a full
    mailbox sweep the first is 0 for every rule:

    `backlog` — messages this rule resolves *right now* (the ones
    `bulk_resolve`/`bulk_negative` will actually UPDATE, all of which have
    `classified_at IS NULL`).

    `already_classified` — messages carrying the same attribute that have
    already been through the pipeline. This rule does not touch them; a bulk
    rule never silently overwrites an existing classification. Shown so a
    lone "0 messages" reads as "this is for mail that hasn't arrived yet"
    rather than as a broken form.
    """

    backlog: int
    already_classified: int


def preview_fanout(
    repos: Any, *, domain: str | None = None, address: str | None = None
) -> FanoutPreview:
    """What `learn_rule` would touch, without writing.

    Uses the same predicate the UPDATE uses — `MessageRepository.count_fanout`
    shares `_fanout_predicate` with both bulk paths — so the number shown is
    the number that will move.
    """
    _match(domain, address)
    total = int(
        repos.messages.count_fanout(
            sender_domain=domain,
            sender_address=address,
            recipient_address=address,
            backlog_only=False,
        )
    )
    backlog = int(
        repos.messages.count_fanout(
            sender_domain=domain,
            sender_address=address,
            recipient_address=address,
        )
    )
    return FanoutPreview(backlog=backlog, already_classified=total - backlog)


def learn_rule(
    conn: psycopg.Connection[Any],
    repos: Any,
    *,
    domain: str | None = None,
    address: str | None = None,
    verdict: Verdict = "positive",
    company: str | None = None,
    company_domain: str | None = None,
    category: str | None = None,
    kind: str = "employer",
    commit: bool = True,
) -> LearnResult:
    """Persist one sender rule and apply it to the current backlog.

    `commit` exists because the CLI owns its transaction boundary per command
    while the web handler owns one per request; neither should have to guess
    whether the other already committed.
    """
    match_type, value = _match(domain, address)

    company_id: UUID | None = None
    if verdict == "positive":
        if not company and not company_domain:
            raise LearnError("A positive rule needs a company name and/or domain.")
        cdomain = company_domain or domain
        found = repos.companies.by_domain(cdomain) if cdomain else None
        if found is None and company:
            found = repos.companies.by_alias(company.lower())
        if found is None:
            now = datetime.now(UTC)
            found = repos.companies.add(
                Company(
                    canonical_name=company or (cdomain or value),
                    domain=cdomain,
                    kind=kind,  # type: ignore[arg-type]
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        company_id = found.id

    if category:
        repos.sender_categories.add(category, verdict, company_id)
        repos.sender_rules.add(
            SenderRule(match_type=match_type, value=value, category=category)
        )
    else:
        repos.sender_rules.add(
            SenderRule(
                match_type=match_type,
                value=value,
                verdict=verdict,
                company_id=company_id,
            )
        )

    resolved = 0
    by = f"rule:{match_type}:{value}"
    if verdict == "positive":
        assert company_id is not None
        resolved = repos.messages.bulk_resolve(
            sender_domain=domain,
            sender_address=address,
            recipient_address=address,
            company_id=company_id,
            by=by,
        )
    elif verdict == "negative":
        resolved = repos.messages.bulk_negative(
            sender_domain=domain,
            sender_address=address,
            by=by,
        )

    if commit:
        conn.commit()

    return LearnResult(
        match_type=match_type,  # type: ignore[arg-type]
        value=value,
        verdict=verdict,
        company_id=company_id,
        resolved=resolved,
    )
