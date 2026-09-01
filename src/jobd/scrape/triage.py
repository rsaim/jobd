"""Batch triage: cheap label-only prefilter before full classification.

The problem: every undecided message pays for a full extraction (subject + body
+ metadata → company/stage/role). Noise that the metadata prefilter cannot
catch — personal mail, non-job transactional receipts, marketing — costs the
same as genuine job mail, token for token. The old BANK_DOMAINS list addressed
this by hardcoding verdicts; with learned rules replacing it, we need a cheaper
gate before the model reads the full body.

The solution: batch triage, inspired by audit.py's proven pattern. After
classify_pending's free tiers settle what they can (rules, prefilter,
thread/rule-carry, the stored thread cache — triage must never charge a
message the free tier already answered), batch the remaining paid-call
candidates 30 at a time and render each with a 240-char snippet (subject +
body prefix). Extract label-only: job_related true/false, no
company/stage/role. Negatives are filtered out immediately; positives pass
to the existing classify pipeline for full extraction.

Token savings: ~1/30th per noise message (one 240-char batch slot vs. one
2000+ char full extraction). On a corpus where 8% is noise, this cuts ~7% of
undecided-tier tokens. Combined with learned rules (which absorb repeat
senders), the two-pass cost delta narrows further.

Pattern: Same as audit.py — 30-message batches, 240-char snippets, numbered
verdicts. No two-pass confirmation: the full classify tier is the confirmation,
so a triage false-positive (noise marked job-related) just pays the full
extraction it would have paid without triage. A false-negative (job mail marked
noise) is the unacceptable error, measured in the triage eval.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from jobd.domain.envelope import body_text
from jobd.domain.raw import RawMessage
from jobd.domain.record import Message
from jobd.domain.style import FIELD_RULES
from jobd.ports import LLMProvider, Storage

#: Snippet length for triage rendering — same as audit.py's measured sweet spot.
_SNIPPET = 240

#: Batch size for triage — same as audit.py.
_BATCH = 30

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "job_related"],
                "properties": {
                    "n": {"type": "integer"},
                    "job_related": {"type": "boolean"},
                },
            },
        }
    },
}

TRIAGE_PROMPT = """\
You are pre-filtering a mailbox for a job-search CRM. Each numbered item is \
one email message — sender, subject, and a body snippet. Decide for each: is \
it plausibly about THIS PERSON'S OWN job search, candidacy, or employment \
(application, interview, offer, rejection, recruiter conversation, onboarding, \
internal work mail)?

Job-related: application confirmations, interview scheduling, recruiter \
outreach, offer letters, rejections, ATS/portal notifications, LinkedIn InMail \
about roles, assessment/coding challenge invites, internal team mail at a \
company they joined.

NOT job-related: marketing/newsletters, product updates, transactional \
receipts (purchases, deliveries, ride confirmations, food orders), account \
security notices, billing/invoices for services, bank/credit card statements, \
package tracking, social media notifications, calendar invites unrelated to \
interviews.

This is a conservative filter. When uncertain or when the snippet is too short \
to judge, mark job_related=true — the next tier reads the full message. Only \
mark false when the snippet clearly shows noise.

Return a verdict for every item number you were given.

""" + FIELD_RULES + "\n"


@dataclass(slots=True)
class TriageResult:
    """Counts for one triage batch."""

    seen: int = 0
    #: Marked job_related=false by triage — filtered out before full classify.
    triaged_negative: int = 0
    #: Marked job_related=true — passed to full classify tier.
    triaged_positive: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)


def _snippet(body: str | None, subject: str | None, chars: int = _SNIPPET) -> str:
    """Render one message as a snippet for triage — same logic as audit.py."""
    text = (body or "").strip().replace("\n", " ")
    return (text or subject or "")[:chars]


def _extract_retry(
    model: LLMProvider, prompt: str, schema: dict[str, Any], text: str
) -> dict[str, Any]:
    """One retry on a parse failure — same as audit.py.

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


def _prefetch(
    storage: Storage, messages: list[Message], workers: int
) -> dict[str, RawMessage | Exception]:
    """Fetch every message's raw bytes concurrently — same as classify.py."""
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
            except Exception as exc:
                out[key] = exc
    return out


def triage_batch(
    messages: list[Message],
    *,
    llm: LLMProvider,
    storage: Storage | None = None,
    raws: Mapping[str, RawMessage | Exception] | None = None,
    fetch_workers: int = 32,
) -> tuple[set[UUID], TriageResult]:
    """Run triage on a batch of messages. Returns (ids to skip, result).

    The returned set contains message IDs marked job_related=false by triage.
    The caller should filter these out before passing the remainder to
    classify_pending for full extraction.

    Args:
        messages: Unclassified messages to triage (batch size controlled by
            caller; recommend 100-300 to amortize the prefetch).
        llm: Triage extractor (flash model recommended).
        storage: Storage port for fetching raw message bytes. Optional when
            `raws` covers every message.
        raws: Already-fetched raw bytes by storage key — classify_pending
            prefetches the identical bytes for its own phases, and fetching
            them from S3 twice doubled the round trips for nothing. Keys
            missing here fall back to `storage`.
        fetch_workers: Thread-pool size for S3 prefetch.

    Returns:
        (skip_ids, result) where skip_ids is the set of message IDs to filter
        out (triaged as noise), and result holds the counts.
    """
    result = TriageResult()
    skip_ids: set[UUID] = set()
    if not messages:
        return skip_ids, result

    fetched: dict[str, RawMessage | Exception] = dict(raws) if raws else {}
    missing = [m for m in messages if m.storage_key not in fetched]
    if missing:
        if storage is None:
            raise ValueError(
                "triage_batch: no storage to fetch "
                f"{len(missing)} keys absent from raws"
            )
        fetched.update(_prefetch(storage, missing, fetch_workers))

    # Batch into 30-message chunks for triage extraction.
    for start in range(0, len(messages), _BATCH):
        batch = messages[start : start + _BATCH]
        result.seen += len(batch)

        # Render each message as a numbered snippet.
        rows: list[tuple[Message, str, str, str]] = []
        for msg in batch:
            raw = fetched.get(msg.storage_key)
            if raw is None or isinstance(raw, Exception):
                # Prefetch failed — skip this message (it will error in
                # classify_pending anyway, so triaging it is moot).
                continue
            payload = raw.payload
            text = body_text(payload)
            # Extract sender using email module directly (same pattern as
            # classify.py's _header, but avoiding circular import).
            import email
            msg_obj = email.message_from_bytes(payload, policy=email.policy.default)
            sender = str(msg_obj.get("From") or "")
            subject = msg.subject or ""
            snippet = _snippet(text, subject)
            rows.append((msg, sender, subject, snippet))

        if not rows:
            continue

        # Build the numbered batch prompt.
        numbered = "\n".join(
            f"{n}. from: {sender or '?'} | subject: {subject or ''} | {snippet}"
            for n, (_, sender, subject, snippet) in enumerate(rows)
        )

        # Extract label-only verdicts.
        try:
            payload = _extract_retry(llm, TRIAGE_PROMPT, TRIAGE_SCHEMA, numbered)
            result.llm_calls += 1
        except Exception as exc:
            result.errors.append(f"triage batch {start}: {type(exc).__name__}: {exc}")
            continue

        verdicts: list[dict[str, Any]] = payload.get("verdicts", [])
        for verdict in verdicts:
            n = verdict.get("n")
            if not isinstance(n, int) or not 0 <= n < len(rows):
                continue
            msg, _, _, _ = rows[n]
            if not verdict.get("job_related"):
                skip_ids.add(msg.id)  # type: ignore[arg-type]
                result.triaged_negative += 1
            else:
                result.triaged_positive += 1

    return skip_ids, result
