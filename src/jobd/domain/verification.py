"""What the verification pass is asked for (`services/verify.py`).

Same split as `extraction.py`: the schema and prompt live in the domain, not
in the LLM adapter, because they are a promise about what a finding can say —
not about a vendor. `extraction.py` reads one message; this reads a whole
company's chain and asks a different question — not "what does this message
say" but "was the pipeline right, and what would a human confirm having read
everything."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from jobd.domain.record import CompanyKind, Message
from jobd.domain.style import FIELD_RULES

#: Structured response schema, strict — same discipline as EXTRACTION_SCHEMA:
#: a model that invents a field is a model whose output nobody parsed.
VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "company_name",
        "company_domain",
        "kind",
        "classification_correct",
        "misclassification_notes",
        "reasoning",
        "deterministic_rules",
    ],
    "properties": {
        "company_name": {"type": "string"},
        "company_domain": {"type": ["string", "null"]},
        "kind": {"type": "string", "enum": ["employer", "agency"]},
        "classification_correct": {
            "type": "boolean",
            "description": "True iff every message below genuinely concerns "
            "this entity and is legitimate job-related correspondence.",
        },
        "misclassification_notes": {
            "type": ["string", "null"],
            "description": "Required (may be null) when classification_correct "
            "is true; explain what looks wrong when it is false.",
        },
        "reasoning": {"type": "string"},
        "deterministic_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["match_type", "value", "verdict", "reason"],
                "properties": {
                    "match_type": {"type": "string", "enum": ["domain", "address"]},
                    "value": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["positive", "negative", "undecided"],
                    },
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

PROMPT = """\
You are auditing one company's or agency's entire recorded email chain from a
job seeker's mailbox — every message the pipeline has already linked to this
one entity, oldest first. You are verifying the pipeline's work, not
re-extracting each message independently.

Judge the whole chain together:
- `company_name` / `company_domain` / `kind`: the correct identity for this
  entity. `kind` is "agency" only for a staffing/recruiting firm representing
  other employers — never for an entity you believe *is* the employer itself.
- `classification_correct`: true only if every message below is genuinely
  about *this person's own candidacy or employment* — an application, an
  interview, a recruiter outreach, an offer, a rejection. Everything else is
  false, including:
  - newsletters, marketing, or an unrelated thread that slipped through;
  - a **personal service provider** the sender happens to schedule
    appointments with — a salon, stylist, gym, clinic, dentist, contractor,
    subscription, retailer. Words like "appointment," "reschedule,"
    "manager," "your account," or a friendly, personal tone are NOT evidence
    of employment correspondence on their own — a hair salon confirming a
    reschedule is not a job. Ask specifically: is this person a candidate
    for a role at this entity, or a *customer* of it? Only the former is
    correct.
  If anything looks wrong, set this false and say what in
  `misclassification_notes`.
- `deterministic_rules`: sender rules worth teaching from what you observed
  across the whole chain, not just one message — a domain or exact address,
  and the verdict it deserves. Use "positive" only when you are certain a
  domain is always job-related (skip the model entirely from now on),
  "negative" only when you are certain it never is, "undecided" to keep it
  routed through extraction but protected from generic bulk/marketing
  filters. Propose only rules you are genuinely confident about — an empty
  list is a fine answer, and is the common one.

Return JSON matching the schema. No prose outside it.

""" + FIELD_RULES + "\n"

#: Bounds the per-message text and the total chain length handed to the
#: model — see `services/verify.py`'s module docstring on why this pass
#: reads MAX_MESSAGES at most, not a company's full history unbounded.
_MAX_BODY_CHARS = 1200


def render_thread_for_model(
    *, company_name: str, company_domain: str | None, messages: list[Message]
) -> str:
    """The exact text handed to the verifier. `messages` must already be the
    bounded, chronological slice the caller wants read — this function does
    not itself cap or reorder anything."""
    lines = [
        f"Entity on record: {company_name} ({company_domain or 'no domain on file'})",
        f"{len(messages)} message(s), oldest first:",
        "",
    ]
    for m in messages:
        body = (m.body_text or "")[:_MAX_BODY_CHARS]
        lines.append(
            f"--- {m.sent_at.date()} · {m.direction} ·"
            f" {m.sender_address or 'unknown sender'} ---\n"
            f"Subject: {m.subject or '(no subject)'}\n{body}\n"
        )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RuleSuggestion:
    """One candidate `sender_rule` the model proposed from the whole chain."""

    match_type: Literal["domain", "address"]
    value: str
    verdict: Literal["positive", "negative", "undecided"]
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """One model's reading of one company's whole chain."""

    company_name: str
    company_domain: str | None
    kind: CompanyKind
    classification_correct: bool
    misclassification_notes: str | None
    reasoning: str
    rules: list[RuleSuggestion] = field(default_factory=list)


def from_payload(payload: dict[str, Any]) -> VerificationResult:
    """Build a VerificationResult from raw model output, coercing
    defensively — same stance as `extraction.from_payload`: a malformed
    sub-field is dropped rather than raised, so one bad rule suggestion
    doesn't cost the whole pass its verdict."""
    kind = payload.get("kind")
    if kind not in ("employer", "agency"):
        kind = "employer"

    rules: list[RuleSuggestion] = []
    for item in payload.get("deterministic_rules") or []:
        if not isinstance(item, dict):
            continue
        match_type = item.get("match_type")
        verdict = item.get("verdict")
        value = _clean(item.get("value"))
        if match_type not in ("domain", "address") or verdict not in (
            "positive",
            "negative",
            "undecided",
        ):
            continue
        if not value:
            continue
        rules.append(
            RuleSuggestion(
                match_type=match_type,
                value=value.lower(),
                verdict=verdict,
                reason=_clean(item.get("reason")) or "",
            )
        )

    return VerificationResult(
        company_name=_clean(payload.get("company_name")) or "Unknown",
        company_domain=_clean(payload.get("company_domain"), lower=True),
        kind=kind,  # type: ignore[arg-type]
        classification_correct=bool(payload.get("classification_correct", False)),
        misclassification_notes=_clean(payload.get("misclassification_notes")),
        reasoning=_clean(payload.get("reasoning")) or "",
        rules=rules,
    )


def _clean(value: object, *, lower: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "n/a", "unknown"}:
        return None
    return text.lower() if lower else text
