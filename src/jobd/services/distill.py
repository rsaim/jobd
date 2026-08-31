"""Distill model judgments into deterministic sender rules.

The online-learning gap this closes: the pipeline learns *what* the model
decided (auto-taught domain rules, rejected-domain suggestions) but never
*why* — and the "why" is what compiles into rules. This pass mines every
verdict the model has already produced and asks one reasoning-enabled call
per batch to name the mechanical rules hiding in them.

The privacy/economy contract: **nothing the model has already read is ever
re-sent.** The distiller sees per-domain aggregates only — domain, message
count, label distribution, bulk share, company kinds, relationships, and a
short snip of the model's own stored reasoning where one exists. No bodies,
no subjects, no addresses beyond the domain itself.

Distilled rules land as ``source='distilled'`` with verdicts capped at
``negative``/``undecided`` — never ``positive`` (a rule that skips the
extractor entirely stays a human's call to grant). The same guards
`_record`'s auto-teach obeys apply: never the account's own domain, never a
hardcoded-tier domain, never freemail, never a domain that already has a
rule. The verify sweep remains the demotion path if one goes wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DISTILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rules"],
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["domain", "verdict", "reason"],
                "properties": {
                    "domain": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["negative", "undecided", "skip"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

DISTILL_PROMPT = """\
You are turning a job-search classifier's past judgments into deterministic
sender rules. Each numbered candidate is one sender domain with aggregate
evidence: how many messages it sent, how the model labeled them, the bulk
share, and (sometimes) the model's own stored reasoning about a thread from
it. You never see message content — judge the domain, not any email.

For each candidate pick one:
- "negative": mail from this domain is never about this person's own
  candidacy — newsletters, product/marketing mail, job-board digests,
  transactional notices. The evidence must be one-sided: any positive or
  recorded outcome means this is NOT a negative domain.
- "undecided": a real employer or recruiting-relevant domain that should
  skip the generic bulk filters and always get a proper look.
- "skip": not enough evidence, mixed evidence, or a domain rules should
  never touch (freemail, ATS vendors, link shorteners, calendars — real
  candidacies flow through those).

Bias toward "skip": a wrong negative silently hides real mail forever,
which is the worst failure this system has. A domain with fewer than 3
messages, or any recorded/positive signal, is a "skip" unless the reasoning
text itself is decisive.

Return a verdict for every candidate. No prose outside the JSON.
"""

#: Domains rules must never touch, beyond the classifier's hardcoded tiers —
#: real candidacies flow through every one of these.
_NEVER_RULE = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "linkedin.com", "calendly.com", "cal.com", "zoom.us",
    "google.com", "docusign.net", "docusign.com",
}


@dataclass
class DistillResult:
    candidates: int = 0
    llm_calls: int = 0
    taught: list[tuple[str, str, str]] = field(default_factory=list)  # domain, verdict, reason
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _candidates(conn: Any, own_domain: str) -> list[dict[str, Any]]:
    """Per-domain aggregates for every domain the model has judged but no
    rule covers. DB-only; no message content leaves this query."""
    rows = conn.execute(
        """
        WITH judged AS (
            -- Every domain a model has ever formed an opinion about, from
            -- any evidence trail: a direct model classification, an audit
            -- correction, a queue item (pending or resolved), or membership
            -- in a thread the thread-reader covered. This is the backfill:
            -- the whole judged history feeds distillation, not just the
            -- current record's model-classified rows.
            SELECT m.sender_domain AS domain,
                   count(*) AS messages,
                   count(*) FILTER (WHERE m.company_id IS NOT NULL) AS linked,
                   count(*) FILTER (WHERE m.is_bulk) AS bulk,
                   count(DISTINCT m.sender_address) AS senders
            FROM message m
            WHERE m.sender_domain IS NOT NULL AND m.sender_domain <> ''
              AND (m.classified_by LIKE 'openrouter%%'
                   OR m.classified_by LIKE 'audit:%%'
                   OR EXISTS (SELECT 1 FROM review_queue rq
                              WHERE rq.message_id = m.id)
                   OR m.thread_id IN (SELECT thread_id FROM thread_extraction))
            GROUP BY m.sender_domain
        ),
        readings AS (
            -- Thread-reading verdicts aggregated over every member of a
            -- covered thread, not just the message the reader happened to
            -- render — a reading speaks for its whole thread.
            SELECT m.sender_domain AS domain,
                   count(DISTINCT te.thread_id)
                       FILTER (WHERE te.label = 'negative') AS neg,
                   count(DISTINCT te.thread_id)
                       FILTER (WHERE te.label = 'positive') AS pos,
                   count(DISTINCT te.thread_id)
                       FILTER (WHERE te.label = 'unclassified') AS punt,
                   string_agg(DISTINCT te.relationship, ', ') AS relationships,
                   string_agg(DISTINCT te.company_kind, ', ') AS kinds,
                   left(string_agg(DISTINCT te.reasoning, ' | '), 400)
                       AS reasoning
            FROM thread_extraction te
            JOIN message m ON m.thread_id = te.thread_id
            WHERE m.sender_domain IS NOT NULL AND m.sender_domain <> ''
            GROUP BY 1
        )
        SELECT j.domain, j.messages, j.linked, j.bulk, j.senders,
               coalesce(r.neg, 0), coalesce(r.pos, 0), coalesce(r.punt, 0),
               r.relationships, r.kinds, r.reasoning
        FROM judged j
        LEFT JOIN readings r ON r.domain = j.domain
        WHERE NOT EXISTS (SELECT 1 FROM sender_rule sr
                          WHERE sr.match_type = 'domain'
                            AND lower(sr.value) = j.domain)
        ORDER BY j.messages DESC
        """,
    ).fetchall()
    out = []
    for (domain, messages, linked, bulk, senders,
         neg, pos, punt, relationships, kinds, reasoning) in rows:
        domain = domain.lower()
        if domain in _NEVER_RULE or domain == own_domain:
            continue
        out.append({
            "domain": domain,
            "messages": messages,
            "linked": linked,
            "bulk": bulk,
            "senders": senders,
            "neg": neg,
            "pos": pos,
            "punt": punt,
            "relationships": relationships,
            "kinds": kinds,
            "reasoning": reasoning,
        })
    return out


def _render(batch: list[dict[str, Any]]) -> str:
    lines = []
    for n, c in enumerate(batch):
        line = (
            f"{n}. {c['domain']} — {c['messages']} messages from "
            f"{c['senders']} senders | {c['bulk']} bulk-marked | "
            f"{c['linked']} linked to a company | thread verdicts: "
            f"{c['neg']} negative, {c['pos']} positive, {c['punt']} punts"
        )
        if c["relationships"]:
            line += f" | relationships: {c['relationships']}"
        if c["kinds"]:
            line += f" | kinds: {c['kinds']}"
        if c["reasoning"]:
            line += f"\n   model's stored reasoning: {c['reasoning']}"
        lines.append(line)
    return "\n".join(lines)


def distill_rules(
    conn: Any,
    llm: Any,
    *,
    own_address: str,
    apply: bool = True,
    batch_size: int = 30,
    meter: Any = None,
) -> DistillResult:
    """Mine domain aggregates, judge them in batches, teach what survives."""
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    from jobd.services.classify import _is_hardcoded_domain

    result = DistillResult()
    own_domain = own_address.rsplit("@", 1)[-1].lower() if "@" in own_address else ""
    candidates = _candidates(conn, own_domain)
    result.candidates = len(candidates)
    meter.set_total(len(candidates))

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        try:
            payload = llm.extract(DISTILL_PROMPT, DISTILL_SCHEMA, text=_render(batch))
            result.llm_calls += 1
        except Exception as exc:
            result.errors.append(f"batch {start}: {type(exc).__name__}: {exc}")
            meter.error(f"batch {start}: {exc}")
            meter.bump("batch_failed", n=len(batch))
            continue
        allowed = {c["domain"] for c in batch}
        evidence = {c["domain"]: c for c in batch}
        for rule in payload.get("rules", []):
            domain = str(rule.get("domain") or "").strip().lower()
            verdict = rule.get("verdict")
            reason = str(rule.get("reason") or "")[:200]
            if (
                domain not in allowed          # no inventing domains
                or verdict not in ("negative", "undecided")
                or domain in _NEVER_RULE
                or domain == own_domain
                or _is_hardcoded_domain(domain)
            ):
                result.skipped += 1
                meter.bump("skipped")
                continue
            ev = evidence[domain]
            if verdict == "negative" and (ev["pos"] > 0 or ev["linked"] > 0):
                # The model's verdict contradicts the record's own evidence —
                # the record wins. A domain with any positive/linked mail is
                # never taught negative, whatever the reasoning says.
                result.skipped += 1
                meter.bump("vetoed")
                continue
            result.taught.append((domain, verdict, reason))
            meter.bump("taught")
            if apply:
                conn.execute(
                    "INSERT INTO sender_rule (match_type, value, verdict, source)"
                    " VALUES ('domain', %s, %s, 'distilled')"
                    " ON CONFLICT (match_type, lower(value)) DO NOTHING",
                    (domain, verdict),
                )
        if apply:
            conn.commit()
    meter.flush(force=True)
    return result
