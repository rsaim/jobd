"""LLM audit of the record — the accuracy pass the deterministic path can't do.

Two sweeps, both cheap by construction (batched snippets, not full bodies):

* **Message audit** — every message linked to a company is re-judged in
  batches of 30: "is this actually about this person's own candidacy or
  employment?" A miss is unlinked (and its stage events with it), and the
  senders behind repeated misses teach corrective rules — a negative address
  rule for an automated sender, a demotion to `undecided` for a poisoned
  auto-positive domain rule. Live case this exists for: an auto-taught
  `amazon.com -> positive` rule that carried every package-locker
  notification into the record without any content ever being read.

* **Application audit** — companies with more than one application get their
  application list and message evidence shown to the model, which returns
  merge/close/keep actions. Live case: an accepted offer at one company
  followed by post-employment mail that spawned a second open "application"
  (and a second offer on the dashboard) for the same engagement.

Everything is applied in one transaction per company and every change is
emitted as an event + fact, so the dashboard shows the cleanup happening.
Dry-run returns the plan without touching anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from jobd.domain.style import FIELD_RULES

#: Snippet budget per message shown to the judge. Subjects carry most of the
#: signal; 240 chars of body settles the rest without paying for full mail.
_SNIPPET = 240
_BATCH = 30

MESSAGE_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "job_related", "company"],
                "properties": {
                    "n": {"type": "integer"},
                    "job_related": {"type": "boolean"},
                    #: When job_related but about a DIFFERENT company than the
                    #: header names, the company it is actually about — the
                    #: relink signal. Null when the link is right (or when
                    #: not job-related at all).
                    "company": {"type": ["string", "null"]},
                },
            },
        }
    },
}

MESSAGE_AUDIT_PROMPT = """\
You are auditing a job-search CRM. Each numbered item is one email already \
linked to the company named in the header — sender, subject, and a body \
snippet. Decide for each: is it genuinely about THIS PERSON'S OWN candidacy \
or employment at that company (application, interview, offer, rejection, \
recruiter conversation, onboarding, internal work mail at a company they \
joined)?

Not job-related: order/shipping/delivery notifications, package pickups, \
receipts, marketing, product updates, account/security notices, newsletters \
— even when they come from the same company's domain. Judge the content, \
not the sender.

If a message IS about the person's candidacy but plainly at a DIFFERENT \
company than the header names (e.g. a recruiter thread about "Tech Lead at \
Preql" filed under some other company), set job_related=true and `company` \
to the company it is actually about. When the link is correct, `company` \
stays null. Interview confirmations, scheduling mail, and portal/assessment \
notifications for the named company ARE job-related — do not flag those.

Return a verdict for every item number you were given.

""" + FIELD_RULES + "\n"

APP_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["company", "application", "action"],
                "properties": {
                    "company": {"type": "integer"},
                    "application": {"type": "integer"},
                    "action": {"type": "string", "enum": ["keep", "merge", "close"]},
                    "into": {"type": ["integer", "null"]},
                    "outcome": {
                        "type": ["string", "null"],
                        "enum": ["rejected", "withdrawn", "accepted", "declined", None],
                    },
                    "reason": {"type": ["string", "null"]},
                },
            },
        }
    },
}

APP_AUDIT_PROMPT = """\
You are auditing one company's applications in a job-search CRM. The person \
may have interviewed several separate times (keep those separate), but two \
common errors need fixing:

1. Mail arriving AFTER an accepted offer (onboarding, first-week logistics, \
internal mail as an employee) sometimes spawned a spurious new "application" \
— often role-less or with a team name as the role, and sometimes even a \
bogus second "offer". That is a continuation of the accepted engagement: \
action "merge" with `into` = the accepted application.
2. One real interview process split across two rows (same role family, \
overlapping or adjacent dates, no distinct outcomes): "merge" into the \
older row. Title variants are the SAME role family — "Senior Software \
Engineer" vs "Senior Software Engineer - AI", a team name in place of a \
title, a seniority-prefix difference. Differing title wording alone never \
justifies "keep" when the dates overlap; and two rows at the same company \
whose offer/decline events land within the same month are one process \
regardless of what their titles say.

A genuinely separate later candidacy (clearly a new role, after the earlier \
one ended with a rejection/withdrawal, distinct process) stays "keep". Use \
"close" with an outcome only when the messages plainly state the process \
ended but the row is still open.

Several companies may appear in one request, each with its own numbered \
application list — every action names both the company number and the \
application number within it. Merges never cross companies.

Return one action per application, for every company shown.

""" + FIELD_RULES + "\n"


@dataclass(slots=True)
class AuditResult:
    companies_checked: int = 0
    companies_deleted: int = 0
    companies_renamed: int = 0
    companies_merged: int = 0
    messages_checked: int = 0
    messages_unlinked: int = 0
    messages_relinked: int = 0
    stage_events_removed: int = 0
    rules_taught: int = 0
    rules_demoted: int = 0
    applications_merged: int = 0
    applications_closed: int = 0
    llm_calls: int = 0
    #: (company, action, detail) rows for the CLI table / dry-run plan.
    plan: list[tuple[str, str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _snippet(body: str | None, subject: str | None, chars: int = _SNIPPET) -> str:
    text = (body or "").strip().replace("\n", " ")
    return (text or subject or "")[:chars]


def _extract_retry(
    model: Any, prompt: str, schema: dict[str, Any], text: str
) -> dict[str, Any]:
    """One retry on a parse failure, none on anything else.

    Strict-schema mode still yields the occasional broken body (live: two
    JSONDecodeErrors in five companies on gemini-flash), and a fresh sample
    almost always parses. API errors are not retried — the provider already
    owns that policy, and doubling a rate-limit is how a sweep stalls.
    `ValueError` covers both `json.JSONDecodeError` and the provider's own
    empty-content raise."""
    try:
        return model.extract(prompt, schema, text=text)
    except ValueError:
        return model.extract(prompt, schema, text=text)


def audit_messages(
    conn: psycopg.Connection[Any],
    llm: Any,
    *,
    judge: Any = None,
    emit: Any = None,
    apply: bool = True,
    company_ids: list[UUID] | None = None,
    since: datetime | None = None,
    out: AuditResult | None = None,
    meter: Any = None,
    workers: int = 8,
    credit_guard: Any = None,
) -> AuditResult:
    """Re-judge recorded messages company by company; unlink the misses.

    ``company_ids``/``since`` scope the sweep (the scrape graph audits only
    companies touched this run; the CLI defaults to everything). Corrective
    rules are taught when one sender produced 3+ misses — that is the fanout
    lesson: fix the attribute, not just the rows.
    """
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    result = out or AuditResult()
    where = ""
    params: dict[str, Any] = {}
    if company_ids is not None:
        where, params = "WHERE c.id = ANY(%(ids)s)", {"ids": company_ids}
    elif since is not None:
        where, params = (
            "WHERE c.last_seen_at >= %(since)s OR c.created_at >= %(since)s",
            {"since": since},
        )
    companies = conn.execute(
        f"""
        SELECT c.id, c.canonical_name FROM company c
        {where}
        ORDER BY (SELECT count(*) FROM message m WHERE m.company_id = c.id) DESC
        """,
        params,
    ).fetchall()

    confirm_model = judge or llm

    def _judge_company(job: tuple[Any, str, list[Any]]) -> dict[str, Any]:
        """The LLM-only half of one company's audit — runs in the pool.

        No DB access in here: the pool exists to multiply the network wait,
        and every row write stays on the caller's thread and connection.
        The adversarial split is unchanged — the cheap tier proposes over
        short snippets, the judge disposes over fuller ones (live-caught:
        flash-lite once flagged a real "SoFi Video Interview Confirmation"
        for unlinking; the fuller re-read is what catches that).
        """
        _company_id, name, rows = job
        found: dict[str, Any] = {
            "name": name, "llm_calls": 0, "checked": 0,
            "errors": [], "proposals": [],
        }

        def ask(model: Any, batch: list[Any], chars: int) -> list[dict[str, Any]]:
            numbered = "\n".join(
                f"{n}. from: {sender or '?'} | subject: {subject or ''} | "
                f"{_snippet(body, subject, chars)}"
                for n, (_id, sender, subject, body) in enumerate(batch)
            )
            payload = _extract_retry(
                model,
                MESSAGE_AUDIT_PROMPT,
                MESSAGE_AUDIT_SCHEMA,
                f"Company: {name}\n\n{numbered}",
            )
            found["llm_calls"] += 1
            verdicts: list[dict[str, Any]] = payload.get("verdicts", [])
            return verdicts

        flagged: list[tuple[Any, str, str, Any]] = []  # row, sender, subject, hint
        for start in range(0, len(rows), _BATCH):
            batch = rows[start : start + _BATCH]
            try:
                verdicts = ask(llm, batch, _SNIPPET)
            except Exception as exc:  # one bad batch never stops the sweep
                found["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            found["checked"] += len(batch)
            for verdict in verdicts:
                n = verdict.get("n")
                if not isinstance(n, int) or not 0 <= n < len(batch):
                    continue
                wrong_company = verdict.get("company")
                if verdict.get("job_related") and not wrong_company:
                    continue
                row = batch[n]
                flagged.append(
                    (row, (row[1] or "").lower(), row[2] or "", wrong_company)
                )

        for start in range(0, len(flagged), _BATCH):
            fbatch = flagged[start : start + _BATCH]
            try:
                verdicts = ask(confirm_model, [f[0] for f in fbatch], 700)
            except Exception as exc:
                found["errors"].append(
                    f"{name} confirm: {type(exc).__name__}: {exc}"
                )
                continue
            for verdict in verdicts:
                n = verdict.get("n")
                if not isinstance(n, int) or not 0 <= n < len(fbatch):
                    continue
                row, sender, subject, hint = fbatch[n]
                found["proposals"].append(
                    {
                        "row_id": row[0],
                        "sender": sender,
                        "subject": subject,
                        "hint": hint,
                        "target": verdict.get("company"),
                        "job_related": bool(verdict.get("job_related")),
                    }
                )
        return found

    from concurrent.futures import ThreadPoolExecutor

    # Waves bound memory: a wave's worth of 800-char snippets is all that is
    # ever held, so the same code walks a 590-company record and a
    # millions-of-messages one. Within a wave the pool judges companies
    # concurrently; applies run serially on this thread as results land.
    wave = max(workers * 4, 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for wstart in range(0, len(companies), wave):
            if credit_guard is not None and not credit_guard.ok():
                # Wave boundary: everything judged so far is applied, and
                # the balance line names why the sweep stopped short.
                result.errors.append(
                    "stopped mid-phase: OpenRouter balance under the credit "
                    f"floor after {result.companies_checked} companies."
                )
                meter.set_counter("stopped", "low-credits mid-messages")
                break
            balance = credit_guard.balance() if credit_guard is not None else None
            if balance is not None:
                meter.set_counter("credits_left", round(balance, 2))
            jobs: list[tuple[Any, str, list[Any]]] = []
            for company_id, name in companies[wstart : wstart + wave]:
                rows = conn.execute(
                    """
                    SELECT m.id, m.sender_address, m.subject, left(m.body_text, 800)
                    FROM message m WHERE m.company_id = %s
                    ORDER BY m.sent_at
                    """,
                    (company_id,),
                ).fetchall()
                if not rows:
                    meter.bump("msg_empty")
                    continue
                jobs.append((company_id, name, rows))

            for found in pool.map(_judge_company, jobs):
                name = found["name"]
                result.companies_checked += 1
                meter.bump("msg_companies")
                result.llm_calls += found["llm_calls"]
                result.messages_checked += found["checked"]
                meter.set_counter("messages_checked", result.messages_checked)
                for err in found["errors"]:
                    result.errors.append(err)
                    meter.error(err)

                misses: list[tuple[UUID, str]] = []
                relinks: list[tuple[UUID, str, str]] = []
                for p in found["proposals"]:
                    subject = p["subject"]
                    if p["job_related"] and p["target"]:
                        # Relink needs corroboration beyond the judge's lone
                        # opinion — one model's lone opinion moved real
                        # interview mail to an unrelated startup in testing,
                        # and a wrong link is worse than a stale one. Two
                        # accepted forms: both tiers independently naming the
                        # same company, or deterministic evidence from the
                        # record itself — the sender's domain IS the
                        # target's, or the thread already holds mail filed at
                        # the target. The deterministic path is the fix for
                        # the gate's known cost: the cheap tier's veto used
                        # to bound relink recall by the weaker model even
                        # when raw headers already proved the judge right.
                        target = str(p["target"])
                        agree = (
                            isinstance(p["hint"], str)
                            and p["hint"].strip().lower()
                            == target.strip().lower()
                        )
                        evidence = (
                            "both-tiers"
                            if agree
                            else _relink_evidence(
                                conn, p["row_id"], p["sender"], target
                            )
                        )
                        if not evidence:
                            result.plan.append(
                                (name, "relink?",
                                 f"-> {target} (unconfirmed, skipped)"
                                 f" | {subject[:40]}")
                            )
                            continue
                        relinks.append((p["row_id"], subject, target))
                        result.plan.append(
                            (name, "relink",
                             f"-> {target} [{evidence}] | {subject[:48]}")
                        )
                    elif not p["job_related"]:
                        misses.append((p["row_id"], p["sender"]))
                        result.plan.append(
                            (name, "unlink",
                             f"{p['sender'] or '?'} | {subject[:55]}")
                        )
                    # judge says job_related at this company -> flag dropped

                if emit and (misses or relinks):
                    emit.emit(
                        "fact",
                        text=f"Audit: {name} — {len(misses)} unlinked, "
                        f"{len(relinks)} refiled",
                    )
                if apply and (misses or relinks):
                    with conn.transaction():
                        if misses:
                            ids = [m for m, _ in misses]
                            removed = conn.execute(
                                "DELETE FROM stage_event"
                                " WHERE evidence_message_id = ANY(%s)",
                                (ids,),
                            ).rowcount
                            conn.execute(
                                "UPDATE message SET company_id = NULL,"
                                " application_id = NULL,"
                                " classified_by = 'audit:' || %s WHERE id = ANY(%s)",
                                (getattr(llm, "name", "llm"), ids),
                            )
                            result.stage_events_removed += removed or 0
                            result.messages_unlinked += len(ids)
                            _teach_from_misses(conn, misses, name, result)
                        for message_id, _subject, target in relinks:
                            resolved = _resolve_target(conn, target)
                            if resolved is None:
                                continue  # planned but unresolvable — left in place
                            conn.execute(
                                "DELETE FROM stage_event"
                                " WHERE evidence_message_id = %s",
                                (message_id,),
                            )
                            conn.execute(
                                "UPDATE message SET company_id = %s,"
                                " application_id = NULL,"
                                " classified_by = 'audit:relink' WHERE id = %s",
                                (resolved, message_id),
                            )
                            result.messages_relinked += 1
    if apply:
        conn.commit()
    return result


def _compatible_merge(
    conn: psycopg.Connection[Any], source_id: UUID, target_id: UUID
) -> bool:
    """Deterministic gate on a judge-proposed company merge.

    A merge is destructive, so a lone model opinion is not enough: the two
    rows must also look like the same organization mechanically — same (or
    absent) domain, or clearly-variant names. Live-caught: the judge merged
    Noetica (noetica.ai, its own interview loop) into Niva (an unrelated
    startup pitched by the same agency) purely on conversational overlap.
    """
    import difflib
    import re

    rows = conn.execute(
        "SELECT id, canonical_name, domain FROM company WHERE id = ANY(%s)",
        ([source_id, target_id],),
    ).fetchall()
    if len(rows) != 2:
        return False
    by_id = {r[0]: r for r in rows}
    _, source_name, source_domain = by_id[source_id]
    _, target_name, target_domain = by_id[target_id]
    def root(domain: str) -> str:
        return ".".join(domain.lower().split(".")[-2:])

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    if source_domain and target_domain:
        # Two real, different domains: never merge on a hunch.
        return root(source_domain) == root(target_domain)
    a, b = norm(source_name), norm(target_name)
    if a and b and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.75


def _relink_evidence(
    conn: psycopg.Connection[Any], message_id: UUID, sender: str, target: str
) -> str | None:
    """Deterministic corroboration that `target` is the right home for this
    message — facts from the record, immune to model agreement games:
    the sender's domain is the target company's domain (or a subdomain of
    it), or the message's own thread already holds mail filed at the
    target. Returns the evidence kind for the plan line, or None."""
    resolved = _resolve_target(conn, target)
    if resolved is None:
        return None
    if sender and "@" in sender:
        sender_domain = sender.rsplit("@", 1)[-1].lower().strip(">")
        row = conn.execute(
            "SELECT lower(coalesce(domain, '')) FROM company WHERE id = %s",
            (resolved,),
        ).fetchone()
        company_domain = row[0] if row else ""
        if company_domain and (
            sender_domain == company_domain
            or sender_domain.endswith("." + company_domain)
        ):
            return "sender-domain"
    row = conn.execute(
        """
        SELECT 1 FROM message m1
        JOIN message m2 ON m2.thread_id = m1.thread_id AND m2.id <> m1.id
        WHERE m1.id = %s AND m1.thread_id IS NOT NULL AND m1.thread_id <> ''
          AND m2.company_id = %s
        LIMIT 1
        """,
        (message_id, resolved),
    ).fetchone()
    if row:
        return "thread"
    return None


def _resolve_target(conn: psycopg.Connection[Any], name: str) -> UUID | None:
    """The company a relink names, via the same alias table classification
    uses. No creation here on purpose: an audit's job is repairing links
    between things that exist, and a judge-invented name becoming a company
    row would be a new error channel, not a fix."""
    row = conn.execute(
        """
        SELECT c.id FROM company c
        WHERE lower(c.canonical_name) = lower(%(name)s)
        UNION
        SELECT a.company_id FROM company_alias a WHERE lower(a.alias) = lower(%(name)s)
        LIMIT 1
        """,
        {"name": name.strip()},
    ).fetchone()
    return row[0] if row else None


def _teach_from_misses(
    conn: psycopg.Connection[Any],
    misses: list[tuple[UUID, str]],
    company: str,
    result: AuditResult,
) -> None:
    """Fix the attribute behind repeated misses, not just the rows.

    3+ misses from one address → negative address rule (an automated feed
    like `amazonlockers@`). Any misses under a domain whose rule is an
    auto-taught `positive` → demote it to `undecided`: the whole failure
    mode this audit exists for is a positive domain rule linking mail whose
    content nothing ever read, and `undecided` re-routes future mail to an
    extractor without silencing the domain.
    """
    by_sender: dict[str, int] = {}
    domains: set[str] = set()
    for _, sender in misses:
        if not sender:
            continue
        by_sender[sender] = by_sender.get(sender, 0) + 1
        domains.add(sender.partition("@")[2])
    for sender, count in by_sender.items():
        if count >= 3:
            row = conn.execute(
                """
                INSERT INTO sender_rule (match_type, value, verdict, source)
                VALUES ('address', %s, 'negative', 'audit')
                ON CONFLICT (match_type, lower(value)) DO NOTHING
                RETURNING value
                """,
                (sender,),
            ).fetchone()
            if row:
                result.rules_taught += 1
                result.plan.append((company, "rule", f"negative address {sender}"))
    if domains:
        demoted = conn.execute(
            """
            UPDATE sender_rule SET verdict = 'undecided'
            WHERE match_type = 'domain' AND verdict = 'positive'
              AND source = 'auto' AND value = ANY(%s)
            RETURNING value
            """,
            (list(domains),),
        ).fetchall()
        for (value,) in demoted:
            result.rules_demoted += 1
            result.plan.append((company, "rule", f"demote domain {value} -> undecided"))


def audit_applications(
    conn: psycopg.Connection[Any],
    judge_model: Any,
    *,
    emit: Any = None,
    apply: bool = True,
    company_ids: list[UUID] | None = None,
    out: AuditResult | None = None,
    meter: Any = None,
    workers: int = 8,
) -> AuditResult:
    """Reconcile companies that carry more than one application."""
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    result = out or AuditResult()
    where = ""
    params: dict[str, Any] = {}
    if company_ids is not None:
        where, params = "AND c.id = ANY(%(ids)s)", {"ids": company_ids}
    companies = conn.execute(
        f"""
        SELECT c.id, c.canonical_name FROM company c
        WHERE (SELECT count(*) FROM application a WHERE a.company_id = c.id) > 1
        {where}
        """,
        params,
    ).fetchall()

    # Batched: companies grouped until ~24 applications share one call —
    # the action is per-company, but one giant agency must not monopolize a
    # request (its action list alone can blow the output cap), so oversized
    # companies still go solo.
    APP_BUDGET = 24

    def describe(company_id: UUID, name: str) -> tuple[list[Any], str]:
        apps = conn.execute(
            """
            SELECT a.id, a.role_title, a.started_at, a.ended_at, a.outcome,
                   (SELECT string_agg(s.stage, ',' ORDER BY s.occurred_at)
                    FROM stage_event s WHERE s.application_id = a.id),
                   (SELECT string_agg(left(m.subject, 70), ' | ' ORDER BY m.sent_at)
                    FROM (SELECT subject, sent_at FROM message
                          WHERE application_id = a.id
                          ORDER BY sent_at DESC LIMIT 4) m)
            FROM application a WHERE a.company_id = %s
            ORDER BY a.started_at
            """,
            (company_id,),
        ).fetchall()
        text = f"Company {{c}}: {name}\n" + "\n".join(
            f"  {n}. role: {role or '?'} | started {started:%Y-%m-%d}"
            f"{f' | ended {ended:%Y-%m-%d}' if ended else ' | open'}"
            f"{f' | outcome {outcome}' if outcome else ''}"
            f" | stages: {stages or 'none'} | recent subjects: {subjects or 'none'}"
            for n, (_id, role, started, ended, outcome, stages, subjects) in enumerate(
                apps
            )
        )
        return apps, text

    # Grouping happens serially (DB reads); the one judge call per group runs
    # in a pool; per-company actions apply serially as each group's verdict
    # lands. Same barrier-free shape as the messages phase.
    groups: list[list[tuple[UUID, str, list[Any], str]]] = []
    group: list[tuple[UUID, str, list[Any], str]] = []
    budget = 0
    for company_id, name in companies:
        apps, text = describe(company_id, name)
        if len(apps) < 2:
            meter.bump("app_singles")
            continue
        if budget and budget + len(apps) > APP_BUDGET:
            groups.append(group)
            group, budget = [], 0
        group.append((company_id, name, apps, text))
        budget += len(apps)
        if len(apps) >= APP_BUDGET:
            groups.append(group)
            group, budget = [], 0
    if group:
        groups.append(group)

    def _judge_group(
        g: list[tuple[UUID, str, list[Any], str]],
    ) -> dict[str, Any] | Exception:
        blocks = [
            text.format(c=index) for index, (_id, _n, _apps, text) in enumerate(g)
        ]
        try:
            return _extract_retry(
                judge_model, APP_AUDIT_PROMPT, APP_AUDIT_SCHEMA, "\n\n".join(blocks)
            )
        except Exception as exc:  # scored against its own group below
            return exc

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for g, payload in zip(groups, pool.map(_judge_group, groups)):
            if isinstance(payload, Exception):
                result.errors.append(
                    f"apps [{', '.join(n for _i, n, _a, _t in g)}]:"
                    f" {type(payload).__name__}: {payload}"
                )
                meter.error(f"apps: {type(payload).__name__}: {payload}")
                continue
            result.llm_calls += 1
            result.companies_checked += len(g)
            meter.bump("app_companies", n=len(g))
            for action in payload.get("actions", []):
                c = action.get("company")
                if not isinstance(c, int) or not 0 <= c < len(g):
                    continue
                _company_id, name, apps, _text = g[c]
                _apply_app_action(conn, action, apps, name, result, apply, emit)
    if apply:
        conn.commit()
    return result


def _apply_app_action(
    conn: psycopg.Connection[Any],
    action: dict[str, Any],
    apps: list[Any],
    name: str,
    result: AuditResult,
    apply: bool,
    emit: Any,
) -> None:
    """One merge/close/keep action against one company's application list."""
    n = action.get("application")
    if not isinstance(n, int) or not 0 <= n < len(apps):
        return
    app_id = apps[n][0]
    kind = action.get("action")
    if kind == "merge":
        into_n = action.get("into")
        if not isinstance(into_n, int) or not 0 <= into_n < len(apps) or into_n == n:
            return
        into_id = apps[into_n][0]
        result.plan.append(
            (name, "merge", f"{apps[n][1] or '?'} -> {apps[into_n][1] or '?'}"
             f" ({action.get('reason') or ''})")
        )
        if apply:
            with conn.transaction():
                # The survivor keeps its identity but inherits what it
                # lacks: a merge that eats the row carrying the only real
                # role title must not blank the offer list (live-caught:
                # a company's title vanished).
                conn.execute(
                    """
                    UPDATE application a SET
                        role_title = coalesce(a.role_title, b.role_title),
                        outcome    = coalesce(a.outcome, b.outcome),
                        ended_at   = coalesce(a.ended_at, b.ended_at)
                    FROM application b
                    WHERE a.id = %s AND b.id = %s
                    """,
                    (into_id, app_id),
                )
                # The survivor may already hold the same (stage, evidence)
                # row — both apps were often minted from the same thread.
                # Drop the duplicates before the move instead of tripping
                # stage_event_evidence_key (live-caught: this crashed a
                # whole audit run mid-phase).
                conn.execute(
                    """
                    DELETE FROM stage_event s
                    WHERE s.application_id = %(src)s
                      AND EXISTS (SELECT 1 FROM stage_event t
                                  WHERE t.application_id = %(dst)s
                                    AND t.stage = s.stage
                                    AND t.evidence_message_id
                                        IS NOT DISTINCT FROM
                                        s.evidence_message_id)
                    """,
                    {"src": app_id, "dst": into_id},
                )
                conn.execute(
                    "UPDATE stage_event SET application_id = %s"
                    " WHERE application_id = %s",
                    (into_id, app_id),
                )
                # A bogus duplicate "offer" merged onto an accepted
                # engagement is noise, not a second offer: drop offer
                # events dated after the surviving app's acceptance.
                conn.execute(
                    """
                    DELETE FROM stage_event s
                    WHERE s.application_id = %(into)s AND s.stage = 'offer'
                      AND EXISTS (SELECT 1 FROM stage_event x
                                  WHERE x.application_id = %(into)s
                                    AND x.stage = 'accepted'
                                    AND x.occurred_at < s.occurred_at)
                    """,
                    {"into": into_id},
                )
                conn.execute(
                    "UPDATE message SET application_id = %s"
                    " WHERE application_id = %s",
                    (into_id, app_id),
                )
                conn.execute("DELETE FROM application WHERE id = %s", (app_id,))
            result.applications_merged += 1
        if emit:
            emit.emit("fact", text=f"Audit: merged duplicate application at {name}")
    elif kind == "close" and action.get("outcome"):
        result.plan.append(
            (name, "close", f"{apps[n][1] or '?'} -> {action['outcome']}")
        )
        if apply:
            conn.execute(
                "UPDATE application SET outcome = %s, ended_at = now()"
                " WHERE id = %s AND ended_at IS NULL",
                (action["outcome"], app_id),
            )
            conn.commit()
            result.applications_closed += 1


COMPANY_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "verdict"],
                "properties": {
                    "n": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "real", "junk", "person", "newsletter",
                            "duplicate", "misnamed",
                        ],
                    },
                    "correct_name": {"type": ["string", "null"]},
                    "duplicate_of": {"type": ["string", "null"]},
                    "reason": {"type": ["string", "null"]},
                },
            },
        }
    },
}

COMPANY_AUDIT_PROMPT = """\
You are verifying the company list of one person's job-search CRM. Each \
numbered dossier is one recorded company: its name, domain, kind, the \
applications under it, and evidence (senders, subjects) from its mail. \
Judge whether each is a GENUINE counterpart in this person's own job \
search — an employer they applied/interviewed at, or a recruiting agency \
that contacted them.

Verdicts:
- "real": a genuine employer or agency engagement, plausibly named.
- "junk": not an organization at all — a sentence fragment or subject-line \
artifact recorded as a name (e.g. "Go.", "On-site", "world class Trading \
firm"), or an aggregation of mail with no candidacy in it.
- "person": named after an individual human, not their organization. If the \
dossier shows the actual organization (domain, signature, subjects), put it \
in correct_name.
- "newsletter": a newsletter/content/platform brand recorded as if it were \
an employer (job-board digests, course platforms, developer newsletters).
- "duplicate": the same organization as another company you can see in THIS \
batch or plainly infer (e.g. same domain root, one name a variant of the \
other) — name the survivor in duplicate_of exactly as its dossier spells it.
- "misnamed": a real engagement whose display name is wrong or sloppy \
(ATS artifact, ALL-CAPS legal suffix, role text as a name) — put the right \
name in correct_name.

Be conservative: when evidence genuinely supports a candidacy, "real" wins \
even if the name is unusual. A staffing agency with many unrelated roles is \
"real" (kind agency), not junk.

Return one verdict per dossier number.

""" + FIELD_RULES + "\n"


def audit_companies(
    conn: psycopg.Connection[Any],
    judge: Any,
    *,
    emit: Any = None,
    apply: bool = True,
    company_ids: list[UUID] | None = None,
    out: AuditResult | None = None,
    meter: Any = None,
    workers: int = 8,
) -> AuditResult:
    """Verify every recorded company as an entity — the aggregation-level
    false-positive hunt. The judge sees a compact dossier (name, domain,
    applications, senders, subjects) per company, eight at a time, and the
    verdicts drive deletion (junk/newsletter), rename (person/misnamed), or
    merge (duplicate)."""
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    result = out or AuditResult()
    where = ""
    params: dict[str, Any] = {}
    if company_ids is not None:
        where, params = "WHERE c.id = ANY(%(ids)s)", {"ids": company_ids}
    companies = conn.execute(
        f"""
        SELECT c.id, c.canonical_name, c.domain, c.kind,
               (SELECT count(*) FROM message m WHERE m.company_id = c.id),
               (SELECT string_agg(DISTINCT m.sender_address, ', ')
                FROM (SELECT sender_address FROM message
                      WHERE company_id = c.id AND sender_address IS NOT NULL
                      LIMIT 4) m),
               (SELECT string_agg(left(m.subject, 60), ' | ')
                FROM (SELECT subject FROM message WHERE company_id = c.id
                      ORDER BY sent_at DESC LIMIT 5) m),
               (SELECT string_agg(
                        coalesce(a.role_title, '?')
                        || coalesce(' [' || a.outcome || ']', ''), '; ')
                FROM application a WHERE a.company_id = c.id)
        FROM company c
        {where}
        ORDER BY c.canonical_name
        """,
        params,
    ).fetchall()
    if not companies:
        return result

    batch_size = 8
    batches = [
        companies[start : start + batch_size]
        for start in range(0, len(companies), batch_size)
    ]

    def _judge_batch(batch: list[Any]) -> dict[str, Any] | Exception:
        dossiers = "\n\n".join(
            f"{n}. name: {name} | domain: {domain or '?'} | kind: {kind}\n"
            f"   applications: {apps or 'none'}\n"
            f"   messages: {msg_count} | senders: {senders or '?'}\n"
            f"   subjects: {subjects or 'none'}"
            for n, (_id, name, domain, kind, msg_count, senders, subjects, apps)
            in enumerate(batch)
        )
        try:
            return _extract_retry(
                judge, COMPANY_AUDIT_PROMPT, COMPANY_AUDIT_SCHEMA, dossiers
            )
        except Exception as exc:  # scored against its own batch below
            return exc

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(_judge_batch, batches))
    for batch, payload in zip(batches, payloads):
        if isinstance(payload, Exception):
            result.errors.append(f"companies: {type(payload).__name__}: {payload}")
            meter.error(f"companies: {type(payload).__name__}: {payload}")
            meter.bump("co_batch_failed", n=len(batch))
            continue
        result.llm_calls += 1
        result.companies_checked += len(batch)
        meter.bump("co_companies", n=len(batch))
        for verdict in payload.get("verdicts", []):
            n = verdict.get("n")
            if not isinstance(n, int) or not 0 <= n < len(batch):
                continue
            company_id, name = batch[n][0], batch[n][1]
            kind = verdict.get("verdict")
            reason = verdict.get("reason") or ""
            if kind == "real":
                continue
            if kind in ("junk", "newsletter"):
                result.plan.append((name, f"delete:{kind}", reason[:90]))
                if apply:
                    with conn.transaction():
                        # Clear classification rather than tombstone: these
                        # messages may hold a real candidacy behind a garbage
                        # entity (an agency pitch whose "company" was a
                        # subject-line fragment). Re-deriving them through
                        # today's classifier — agency fallback, learned
                        # rules, guards that postdate the original pass — is
                        # the self-healing loop; deleting the link forever
                        # would just lose the thread.
                        conn.execute(
                            "UPDATE message SET application_id = NULL,"
                            " classified_at = NULL, classified_by = NULL"
                            " WHERE company_id = %s",
                            (company_id,),
                        )
                        conn.execute("DELETE FROM company WHERE id = %s", (company_id,))
                    result.companies_deleted += 1
                if emit:
                    emit.emit("fact", text=f"Audit: removed {kind} company {name}")
            elif kind in ("person", "misnamed"):
                correct = (verdict.get("correct_name") or "").strip()
                if not correct or correct.lower() == name.lower():
                    result.plan.append((name, f"flag:{kind}", reason[:90]))
                    continue
                result.plan.append((name, "rename", f"-> {correct} ({reason[:60]})"))
                if apply:
                    with conn.transaction():
                        conn.execute(
                            "UPDATE company SET canonical_name = %s WHERE id = %s",
                            (correct, company_id),
                        )
                        conn.execute(
                            "INSERT INTO company_alias (company_id, alias, source)"
                            " VALUES (%s, lower(%s), 'audit')"
                            " ON CONFLICT (lower(alias)) DO NOTHING",
                            (company_id, correct),
                        )
                    result.companies_renamed += 1
                if emit:
                    emit.emit("fact", text=f"Audit: renamed {name} -> {correct}")
            elif kind == "duplicate":
                target_name = (verdict.get("duplicate_of") or "").strip()
                target = _resolve_target(conn, target_name) if target_name else None
                if target is None or target == company_id:
                    detail = f"of {target_name or '?'} (unresolved)"
                    result.plan.append((name, "flag:duplicate", detail))
                    continue
                if not _compatible_merge(conn, company_id, target):
                    detail = f"of {target_name} (incompatible domains/names, skipped)"
                    result.plan.append((name, "flag:duplicate", detail))
                    continue
                result.plan.append((name, "merge-company", f"-> {target_name}"))
                if apply:
                    with conn.transaction():
                        conn.execute(
                            "UPDATE message SET company_id = %s WHERE company_id = %s",
                            (target, company_id),
                        )
                        conn.execute(
                            "UPDATE application SET company_id = %s"
                            " WHERE company_id = %s",
                            (target, company_id),
                        )
                        conn.execute(
                            "UPDATE company_alias SET company_id = %s"
                            " WHERE company_id = %s"
                            " AND lower(alias) NOT IN (SELECT lower(alias)"
                            "   FROM company_alias WHERE company_id = %s)",
                            (target, company_id, target),
                        )
                        conn.execute("DELETE FROM company WHERE id = %s", (company_id,))
                    result.companies_merged += 1
                if emit:
                    emit.emit("fact", text=f"Audit: merged {name} into {target_name}")
    if apply:
        # Callers vary: the CLI's connection context commits on exit, an
        # ad-hoc connection does not — live-caught as a whole sweep of
        # deletions silently rolling back. Applied work commits here.
        conn.commit()
    return result


def run_audit(
    conn: psycopg.Connection[Any],
    llm: Any,
    *,
    judge: Any = None,
    emit: Any = None,
    apply: bool = True,
    company_ids: list[UUID] | None = None,
    since: datetime | None = None,
    meter: Any = None,
    workers: int = 8,
    credit_guard: Any = None,
) -> AuditResult:
    """Both sweeps, one result. Messages first — an unlinked message can
    change what the application audit sees.

    ``judge`` is the application-audit model, defaulting to ``llm``. Split on
    purpose: message verdicts are high-volume factual reads a cheap model
    handles, but application reconciliation is one structural judgment per
    company and measurably needs the stronger head — tested live, the cheap
    tier missed a continuation-merge the bigger one caught.
    """
    if meter is None:
        from jobd.services.metrics import NullMeter

        meter = NullMeter()
    result = AuditResult()
    # ETA denominator: each phase walks the company list roughly once (the
    # applications phase counts singles too, see its `app_singles` bump), so
    # 3× the company count is the honest order-of-magnitude total.
    n_companies = conn.execute("SELECT count(*) FROM company").fetchone()[0]
    meter.set_total(3 * int(n_companies))
    def _credits_ok(where: str) -> bool:
        # A phase applies everything it finished, so stopping between phases
        # loses nothing — the next run redoes only what never ran. Better
        # than the alternative this replaces: a 402 mid-phase, surfaced as
        # per-company API errors.
        if credit_guard is not None and not credit_guard.ok():
            result.errors.append(
                f"stopped before {where}: OpenRouter balance under the "
                "credit floor — top up and rerun."
            )
            meter.set_counter("stopped", f"low-credits before {where}")
            return False
        return True

    meter.set_counter("phase", "messages")
    audit_messages(
        conn, llm, judge=judge, emit=emit, apply=apply, company_ids=company_ids,
        since=since, out=result, meter=meter, workers=workers,
        credit_guard=credit_guard,
    )
    if not _credits_ok("applications"):
        meter.flush(force=True)
        return result
    meter.set_counter("phase", "applications")
    audit_applications(
        conn, judge or llm, emit=emit, apply=apply, company_ids=company_ids,
        out=result, meter=meter, workers=workers,
    )
    if not _credits_ok("companies"):
        meter.flush(force=True)
        return result
    meter.set_counter("phase", "companies")
    audit_companies(
        conn, judge or llm, emit=emit, apply=apply, company_ids=company_ids,
        out=result, meter=meter, workers=workers,
    )
    for key, value in (
        ("messages_unlinked", result.messages_unlinked),
        ("apps_merged", result.applications_merged),
        ("apps_closed", result.applications_closed),
        ("companies_deleted", result.companies_deleted),
        ("companies_renamed", result.companies_renamed),
        ("companies_merged", result.companies_merged),
    ):
        meter.set_counter(key, value)
    meter.flush(force=True)
    return result
