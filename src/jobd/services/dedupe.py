"""Duplicate-company detection (algorithm-improvements.md #6).

Entity resolution deliberately biases toward *split* over merge: an over-eager
merge fuses two hiring processes into one timeline, while a split leaves two
pages a human can join with one alias. The split bias is right, but its cost is
the human's: finding and joining those pages by hand.

This module proposes merges — it never applies them. It ranks pairs of
companies whose names/domains are close enough to be the same employer and
surfaces them for a human to confirm (or ignore). Every rule is deterministic
and readable; nothing here reaches the network or the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobd.domain.record import Company
from jobd.domain.resolve import is_generic_domain, normalise_company, normalise_domain


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """One proposed merge, ranked for a human to confirm."""

    left_id: Any
    right_id: Any
    left_name: str
    right_name: str
    reason: str
    score: float


def _domain_label(company: Company) -> str | None:
    domain = normalise_domain(company.domain) if company.domain else None
    if not domain or is_generic_domain(domain):
        return None
    return domain.split(".")[0]


def _tokens(name: str | None) -> set[str]:
    return {w for w in normalise_company(name or "").split() if w}


def _domains_conflict(a: Company, b: Company) -> bool:
    """True when both companies carry distinct, real (non-generic) domains that
    cannot be the same entity — a company's own domain is its identity, so two
    different domains are two different companies unless one is a subdomain of
    the other."""
    da = normalise_domain(a.domain) if a.domain else None
    db = normalise_domain(b.domain) if b.domain else None
    if not da or not db or is_generic_domain(da) or is_generic_domain(db):
        return False
    if da == db:
        return False
    return not (da.endswith(f".{db}") or db.endswith(f".{da}"))


def _merge_signal(a: Company, b: Company) -> tuple[str, float] | None:
    """The reason two companies are probably the same, or None.

    Split-biased by construction: only strong, name-or-domain-anchored signals
    propose a merge, and a conflicting domain vetoes it outright.
    """
    if _domains_conflict(a, b):
        return None

    na = normalise_company(a.canonical_name)
    nb = normalise_company(b.canonical_name)
    if na and na == nb:
        return "identical normalised name", 1.0

    ta, tb = _tokens(na), _tokens(nb)
    if ta and tb:
        inter = ta & tb
        containment = len(inter) / min(len(ta), len(tb))
        if containment >= 1.0:
            return "one name contains the other", 0.9
        if containment >= 0.6:
            return "high name overlap", 0.6

    # A company's domain label IS its squashed name — "Runway" the body-name
    # and "runwayml" the domain-label are the same employer even though the
    # strings differ by a suffix the resolver's exact alias match missed.
    la = _domain_label(a)
    lb = _domain_label(b)
    if la and nb and (la == normalise_company(nb).replace(" ", "") or la in normalise_company(nb) or normalise_company(nb) in la):
        return "name matches the other's domain", 0.8
    if lb and na and (lb == normalise_company(na).replace(" ", "") or lb in normalise_company(na) or normalise_company(na) in lb):
        return "name matches the other's domain", 0.8

    return None


def suggest_merges(
    companies: list[Company], *, limit: int = 200
) -> list[MergeCandidate]:
    """Every pair that looks like the same employer, best evidence first.

    O(n²) over companies — the record holds hundreds, not millions, and this
    runs on demand from a CLI, not on the classify hot path. Deterministic, no
    model, no network. Returns candidates sorted by score descending."""
    candidates: list[MergeCandidate] = []
    for i in range(len(companies)):
        for j in range(i + 1, len(companies)):
            a, b = companies[i], companies[j]
            if a.id is None or b.id is None:
                continue
            signal = _merge_signal(a, b)
            if signal is None:
                continue
            reason, score = signal
            candidates.append(
                MergeCandidate(
                    left_id=a.id,
                    right_id=b.id,
                    left_name=a.canonical_name,
                    right_name=b.canonical_name,
                    reason=reason,
                    score=score,
                )
            )
    candidates.sort(key=lambda c: -c.score)
    return candidates[:limit]
