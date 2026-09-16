"""The learning-loop eval: prove the rules the pipeline teaches itself pay.

Two passes over the same gold corpus, against the *real* policy
(`jobd.domain.learning` — the same functions classify.py applies in
production, so this eval cannot drift from what actually gets taught):

    pass 1  cold — no rules. Every non-prefiltered message pays a model
            call; every confident answer teaches (undecided/promote for
            positives, immediate negative rules for noise).
    pass 2  the rules from pass 1 stand in. Learned negatives prefilter
            for free; promoted positives resolve by rule-carry with no
            model call.

What must hold (asserted, exit-non-zero on failure):

  1. model_calls(pass 2) < model_calls(pass 1) — the loop converges on
     cost. This is the eval for the removed BANK_DOMAINS/ATS_DOMAINS
     shortcuts: what was once free by fiat must become free by learning.
  2. F1 and company accuracy do not regress on pass 2 — cheaper must not
     mean worse. (Stage accuracy is reported, not asserted: a rule-carried
     message skips the model and records no stage, the same documented
     trade-off production thread-carry makes.)
  3. At least one negative rule and one positive promotion were actually
     learned — a pass that saves nothing has learned nothing.
  4. Policy scoping (the old GENERIC_DOMAINS verdict): a webmail negative
     teaches an *address* rule, never the domain; a webmail company domain
     is never taught as an identity. Checked directly against the policy —
     no corpus support needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.utils import getaddresses
from typing import Any

from gold_demo import GOLD
from jobd_classify import pred_company, scorecard

from jobd.domain import learning, prefilter
from jobd.domain.extraction import EXTRACTION_SCHEMA, PROMPT, render_for_model
from jobd.services.demo import demo_messages


@dataclass
class RuleState:
    """The in-memory mirror of the sender_rule table for a replay run."""

    known: dict[str, str] = field(default_factory=dict)
    #: company name by taught domain — what rule-carry answers with.
    companies: dict[str, str] = field(default_factory=dict)
    #: domain rule key → (company that first sighting was taught against,
    #: rule source) — the entity-consistency and machine-never-over-human
    #: index the promotion guard consults.
    known_company: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    negatives_learned: int = 0
    undecided_learned: int = 0
    promotions: int = 0


def _sender_address(sender: str) -> str:
    addresses = [a for _, a in getaddresses([sender]) if a]
    return addresses[0].lower() if addresses else ""


def _one_pass(
    extractor: Any, state: RuleState, *, learn: bool
) -> tuple[list[dict[str, Any]], int]:
    """Replay the corpus through prefilter → carry → model, mirroring
    classify._one's routing. Returns (per-message case dicts compatible
    with jobd_classify.scorecard, model_calls)."""
    cases: list[dict[str, Any]] = []
    model_calls = 0
    for i, raw in enumerate(demo_messages()):
        msg = message_from_bytes(raw.payload, policy=policy.default)
        gold = GOLD[i]
        sender = str(msg["From"] or "")
        address = _sender_address(sender)
        labels = raw.metadata.get("labels", "")
        learned = learning.covering_verdict(state.known, address) if address else None
        verdict = prefilter.score(
            sender=sender,
            recipients="",
            learned=learned,  # type: ignore[arg-type]
            gmail_labels=labels,
            has_list_unsubscribe=bool(msg["List-Unsubscribe"]),
        )
        domain = prefilter.domain_of(address)
        if verdict.status == "negative":
            pred = {"label": "negative", "via": "prefilter"}
        elif (
            learned == "positive"
            and domain
            and state.companies.get(domain) is not None
        ):
            # The zero-cost rule-carry: a promoted rule answers company and
            # relatedness with no model call. No stage — same trade-off as
            # production carry paths.
            pred = {
                "label": "positive",
                "stage": None,
                "company_name": state.companies[domain],
                "via": "rule-carry",
            }
        else:
            rendered = render_for_model(
                sender=sender,
                recipient=str(msg["To"] or ""),
                subject=str(msg["Subject"] or ""),
                body=msg.get_content(),
                date=str(msg["Date"] or ""),
                labels=labels,
            )
            payload = extractor.extract(PROMPT, EXTRACTION_SCHEMA, text=rendered)
            model_calls += 1
            pred = {
                "label": payload.get("label"),
                "stage": payload.get("stage"),
                "company_name": pred_company(payload),
                "via": extractor.name,
            }
            if learn:
                _teach(state, pred, payload, address)
        cases.append(
            {
                "metadata": {
                    "pred": pred,
                    "sender": sender,
                    "gold_job_related": gold.job_related,
                    "gold_stage": gold.stage,
                    "gold_company": gold.company,
                }
            }
        )
    return cases, model_calls


def _teach(
    state: RuleState, pred: dict[str, Any], payload: dict[str, Any], address: str
) -> None:
    """Apply the production policy to one model verdict — the same calls
    classify.py's `_record`/`_teach_negative` make, against the same
    functions."""
    if pred["label"] == "negative" and address:
        teach = learning.on_model_negative(
            sender_address=address, known=state.known
        )
        if teach is not None:
            state.known[f"{teach.match_type}:{teach.value}"] = teach.verdict
            state.negatives_learned += 1
        return
    company_domain = (payload.get("company_domain") or "").lower().strip()
    if pred["label"] == "positive" and company_domain:
        company_key = pred.get("company_name")
        teach = learning.on_confident_positive(
            company_domain=company_domain,
            company_key=company_key,
            company_kind=payload.get("company_kind"),
            known=state.known,
            known_company=state.known_company,
        )
        if teach is not None:
            state.known[f"{teach.match_type}:{teach.value}"] = teach.verdict
            if teach.match_type == "domain" and not teach.promotion:
                state.known_company[f"domain:{teach.value}"] = (company_key, "auto")
            if teach.promotion:
                state.promotions += 1
            else:
                state.undecided_learned += 1
            state.companies.setdefault(
                teach.value, payload.get("company_name") or teach.value
            )


class _Case:
    """Duck-type the one attribute jobd_classify.scorecard reads."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata


def _policy_scoping_checks() -> list[str]:
    """The webmail-guard assertions — the removed GENERIC_DOMAINS verdict,
    checked structurally against the policy itself."""
    failures: list[str] = []
    teach = learning.on_model_negative(
        sender_address="spammer@gmail.com", known={}
    )
    if teach is None or teach.match_type != "address":
        failures.append(
            "webmail negative must teach an address rule, got "
            f"{teach!r} — one spammer would silence all of gmail.com"
        )
    teach = learning.on_model_negative(
        sender_address="billing@somebank.example", known={}
    )
    if teach is None or teach.match_type != "domain":
        failures.append(
            f"corporate negative must teach a domain rule, got {teach!r}"
        )
    if (
        learning.on_confident_positive(
            company_domain="gmail.com",
            company_key="x",
            company_kind="employer",
            known={},
            known_company={},
        )
        is not None
    ):
        failures.append(
            "a shared mailbox provider must never be taught as a company"
        )
    if (
        learning.on_model_negative(
            sender_address="person@taught.example",
            known={"domain:taught.example": "positive"},
        )
        is not None
    ):
        failures.append(
            "a standing rule must never be overwritten by a model negative"
        )
    # The entity-fusion guard: promotion must
    # require that the two confident extractions resolved to the *same*
    # company. A domain that answered to two different companies must not
    # unlock the zero-cost carry path.
    promote = learning.on_confident_positive(
        company_domain="acme.example",
        company_key="co-A",
        company_kind="employer",
        known={"domain:acme.example": "undecided"},
        known_company={"domain:acme.example": ("co-A", "auto")},
    )
    if promote is None or not promote.promotion:
        failures.append(
            "same-company second sighting must promote to positive"
        )
    split = learning.on_confident_positive(
        company_domain="shared.example",
        company_key="co-B",
        company_kind="employer",
        known={"domain:shared.example": "undecided"},
        known_company={"domain:shared.example": ("co-A", "auto")},
    )
    if split is not None:
        failures.append(
            "a domain that resolved to two different companies must never promote"
        )
    agency = learning.on_confident_positive(
        company_domain="staffing.example",
        company_key="co-A",
        company_kind="agency",
        known={"domain:staffing.example": "undecided"},
        known_company={"domain:staffing.example": ("co-A", "auto")},
    )
    if agency is not None:
        failures.append("a recruiting-agency domain must never promote to positive")
    # An `undecided` with no pinned company is a first-sighting-equivalent:
    # one confident extraction is one datapoint, never agreement.
    unpinned = learning.on_confident_positive(
        company_domain="bare.example",
        company_key="co-A",
        company_kind="employer",
        known={"domain:bare.example": "undecided"},
        known_company={},
    )
    if unpinned is not None:
        failures.append(
            "an undecided rule with no pinned company must not promote "
            "on a single extraction"
        )
    # The machine-never-over-human contract (AGENTS.md): a human-taught
    # `undecided` is a standing human decision, not a promotion candidate.
    human = learning.on_confident_positive(
        company_domain="held.example",
        company_key="co-A",
        company_kind="employer",
        known={"domain:held.example": "undecided"},
        known_company={"domain:held.example": ("co-A", "human")},
    )
    if human is not None:
        failures.append("a human-sourced rule must never be auto-promoted")
    return failures


def run_learning(model: str | None) -> int:
    """Run both passes, print the report, return a process exit code."""
    from jobd.adapters.llm import load_provider

    extractor = load_provider(model or "default")
    state = RuleState()

    raw1, calls1 = _one_pass(extractor, state, learn=True)
    raw2, calls2 = _one_pass(extractor, state, learn=False)
    cases1 = [_Case(c["metadata"]) for c in raw1]
    cases2 = [_Case(c["metadata"]) for c in raw2]
    card1, card2 = scorecard(cases1), scorecard(cases2)  # type: ignore[arg-type]

    print(f"\njobd learning eval — {extractor.name}")
    print(f"  {'':24}   pass 1 (cold)   pass 2 (learned)")
    print(f"  {'model calls':24}   {calls1:>10}   {calls2:>13}")
    for key in ("f1", "precision", "recall", "company_accuracy",
                "stage_accuracy", "noise_accuracy"):
        print(f"  {key:24}   {card1[key]:>10.3f}   {card2[key]:>13.3f}")
    print(
        f"  rules learned: {state.undecided_learned} undecided, "
        f"{state.promotions} promoted to positive, "
        f"{state.negatives_learned} negative"
    )

    failures = _policy_scoping_checks()
    if not calls2 < calls1:
        failures.append(
            f"no cost convergence: pass 2 made {calls2} model calls "
            f"vs {calls1} on pass 1"
        )
    eps = 1e-9
    for key in ("f1", "company_accuracy"):
        if card2[key] + eps < card1[key]:
            failures.append(
                f"{key} regressed under learned rules: "
                f"{card1[key]:.3f} -> {card2[key]:.3f}"
            )
    if state.negatives_learned == 0:
        failures.append("no negative rules learned — the noise slice taught nothing")
    if state.promotions == 0:
        failures.append(
            "no undecided -> positive promotions — repeat senders never "
            "earned the zero-cost path"
        )

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    saved = 1 - calls2 / calls1 if calls1 else 0.0
    print(f"\nPASS — learned rules cut model calls by {saved:.0%} at no accuracy loss")
    print(json.dumps({"pass1": card1, "pass2": card2,
                      "model_calls": [calls1, calls2]}, indent=2))
    return 0
