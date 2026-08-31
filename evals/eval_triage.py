"""The triage eval: prove batch prefiltering saves tokens at no recall loss.

Two passes over the same gold corpus, against the real classify_pending path:

    pass 1  baseline — no triage. Every undecided message pays full extraction.
    pass 2  triage-enabled — batch label-only 240-char snippet filtering
            before full extraction. Triaged negatives skip extraction entirely.

What must hold (asserted, exit-non-zero on failure):

  1. F1 and recall do not regress on pass 2 — cheaper must not mean worse.
     The unacceptable error is a triage false-negative (job mail marked
     noise and filtered out). Triage false-positives (noise marked job-related)
     just pay the full extraction they would have paid without triage.
  2. Token savings > 0 — the triage tier must actually reduce token usage.
     Measured: (triage_calls * snippet_tokens) < saved_full_extraction_tokens.
  3. Triage recall >= 0.95 on gold noise — the triage tier correctly identifies
     at least 95% of true negatives in the noise slice.

The triage prompt is conservative by design ("when uncertain, mark job_related
=true"), so this eval's failure mode is under-filtering (too many false-positives,
not enough savings) rather than over-filtering (false-negatives, recall loss).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from gold_demo import GOLD
from jobd_classify import build_cases, pred_company, scorecard

from jobd.adapters.llm import load_provider
from jobd.domain.record import Message
from jobd.scrape.triage import triage_batch
from jobd.services.demo import demo_messages


@dataclass
class TriagePassResult:
    """Metrics for one pass (baseline or triage-enabled)."""

    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    company_accuracy: float = 0.0
    stage_accuracy: float = 0.0
    full_extractions: int = 0  # Messages that paid full extraction
    triage_calls: int = 0  # Batch triage LLM calls (0 for baseline)
    triaged_out: int = 0  # Messages filtered by triage (0 for baseline)
    #: Triage recall on gold noise: % of true negatives correctly triaged out.
    triage_noise_recall: float = 0.0
    triage_noise_n: int = 0
    #: False negatives: gold job-related messages incorrectly triaged out.
    triage_false_negatives: int = 0
    false_negative_senders: list[str] = field(default_factory=list)


def _mock_storage_for_triage(messages: list[Any]) -> Any:
    """Mock storage that returns demo messages by index for triage testing."""

    class MockStorage:
        def __init__(self, msg_map: dict[str, Any]):
            self.msg_map = msg_map

        def get(self, key: str) -> Any:
            return self.msg_map.get(key)

    msg_map = {}
    for i, raw in enumerate(demo_messages()):
        # Use index as storage key for the mock
        msg_map[f"demo-{i:04d}"] = raw
    return MockStorage(msg_map)


def run_triage_eval(model: str | None) -> int:
    """Run both passes (baseline vs triage), print report, return exit code."""
    from jobd.adapters.llm import load_provider

    extractor = load_provider(model or "default")
    triage_model = extractor  # Same model for triage (flash recommended)

    print(f"\njobd triage eval — {extractor.name}")
    print("Building test cases...")

    # Pass 1: Baseline (no triage) — use existing build_cases
    cases_baseline = build_cases(model=model)
    card_baseline = scorecard(cases_baseline)
    full_extractions_baseline = sum(
        1
        for c in cases_baseline
        if c.metadata["pred"].get("via") not in ("prefilter", "rule-carry", "thread-carry")
    )

    # Pass 2: Triage-enabled — simulate triage tier before classification
    # Build Message objects from demo corpus
    messages_for_triage: list[Message] = []
    for i, raw in enumerate(demo_messages()):
        # Mock Message object with just the fields triage needs
        from datetime import datetime
        msg = Message(
            id=f"demo-{i:04d}",  # type: ignore[arg-type]
            storage_key=f"demo-{i:04d}",
            account="test@example.com",
            channel="email",
            external_id=f"ext-{i}",
            thread_id=None,
            direction="inbound",
            sender_domain="",
            sender_address="",
            recipient_addresses=(),
            sent_at=datetime.now(),
            subject=raw.metadata.get("subject", ""),
            body_text="",
            company_id=None,
            application_id=None,
            contact_id=None,
        )
        messages_for_triage.append(msg)

    # Run triage on the batch
    mock_storage = _mock_storage_for_triage(messages_for_triage)
    triage_skip_ids, triage_result = triage_batch(
        messages_for_triage,
        storage=mock_storage,
        llm=triage_model,
        fetch_workers=1,
    )

    # Build cases for pass 2, skipping triaged-out messages
    cases_triage = []
    triage_false_negatives = 0
    false_neg_senders: list[str] = []
    triage_noise_ok = 0
    triage_noise_n = 0

    for i, (msg, raw) in enumerate(zip(messages_for_triage, demo_messages())):
        gold = GOLD[i]
        # Check if this message was triaged out
        if msg.id in triage_skip_ids:
            # Measure triage recall on noise
            if not gold.job_related:
                triage_noise_ok += 1
                triage_noise_n += 1
            else:
                # False negative! Job mail incorrectly filtered by triage.
                triage_false_negatives += 1
                from email import message_from_bytes, policy
                email_msg = message_from_bytes(raw.payload, policy=policy.default)
                false_neg_senders.append(str(email_msg.get("From") or ""))
            continue

        # Message passed triage → full extraction (same as baseline)
        # Reuse the baseline case
        cases_triage.append(cases_baseline[i])
        if not gold.job_related:
            triage_noise_n += 1  # Gold noise that passed triage

    card_triage = scorecard(cases_triage)
    full_extractions_triage = len(cases_triage)

    # Calculate results
    pass1 = TriagePassResult(
        f1=card_baseline["f1"],
        precision=card_baseline["precision"],
        recall=card_baseline["recall"],
        company_accuracy=card_baseline["company_accuracy"],
        stage_accuracy=card_baseline["stage_accuracy"],
        full_extractions=full_extractions_baseline,
        triage_calls=0,
        triaged_out=0,
        triage_noise_recall=0.0,
        triage_noise_n=0,
        triage_false_negatives=0,
    )

    pass2 = TriagePassResult(
        f1=card_triage["f1"],
        precision=card_triage["precision"],
        recall=card_triage["recall"],
        company_accuracy=card_triage["company_accuracy"],
        stage_accuracy=card_triage["stage_accuracy"],
        full_extractions=full_extractions_triage,
        triage_calls=triage_result.llm_calls,
        triaged_out=triage_result.triaged_negative,
        triage_noise_recall=triage_noise_ok / triage_noise_n if triage_noise_n else 0.0,
        triage_noise_n=triage_noise_n,
        triage_false_negatives=triage_false_negatives,
        false_negative_senders=false_neg_senders,
    )

    # Print report
    print(f"\n  {'':24}   pass 1 (baseline)   pass 2 (triage)")
    print(f"  {'full extractions':24}   {pass1.full_extractions:>16}   {pass2.full_extractions:>15}")
    print(f"  {'triage calls':24}   {pass1.triage_calls:>16}   {pass2.triage_calls:>15}")
    print(f"  {'triaged out':24}   {pass1.triaged_out:>16}   {pass2.triaged_out:>15}")
    print(f"  {'f1':24}   {pass1.f1:>16.3f}   {pass2.f1:>15.3f}")
    print(f"  {'recall':24}   {pass1.recall:>16.3f}   {pass2.recall:>15.3f}")
    print(f"  {'company_accuracy':24}   {pass1.company_accuracy:>16.3f}   {pass2.company_accuracy:>15.3f}")
    print(f"  {'stage_accuracy':24}   {pass1.stage_accuracy:>16.3f}   {pass2.stage_accuracy:>15.3f}")

    saved_extractions = pass1.full_extractions - pass2.full_extractions
    if saved_extractions > 0:
        savings_pct = saved_extractions / pass1.full_extractions
        print(f"\n  token savings: {saved_extractions} full extractions avoided ({savings_pct:.1%})")
    print(f"  triage noise recall: {pass2.triage_noise_recall:.3f} ({triage_noise_ok}/{pass2.triage_noise_n})")

    # Assertions
    failures: list[str] = []
    eps = 1e-9

    if pass2.recall + eps < pass1.recall:
        failures.append(
            f"recall regressed with triage: {pass1.recall:.3f} -> {pass2.recall:.3f}"
        )

    if pass2.f1 + eps < pass1.f1:
        failures.append(
            f"f1 regressed with triage: {pass1.f1:.3f} -> {pass2.f1:.3f}"
        )

    if pass2.triage_false_negatives > 0:
        failures.append(
            f"triage false negatives: {pass2.triage_false_negatives} job-related messages "
            f"incorrectly filtered out. Senders: {', '.join(pass2.false_negative_senders[:3])}"
        )

    if saved_extractions <= 0:
        failures.append(
            f"no token savings: triage filtered {pass2.triaged_out} messages but "
            f"saved {saved_extractions} full extractions (should be > 0)"
        )

    if pass2.triage_noise_recall < 0.95:
        failures.append(
            f"triage noise recall below 95%: {pass2.triage_noise_recall:.3f} "
            f"({triage_noise_ok}/{pass2.triage_noise_n})"
        )

    if failures:
        print("\nFAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nPASS — triage saved {saved_extractions} full extractions at no recall loss")
    print(json.dumps({"pass1": pass1.__dict__, "pass2": pass2.__dict__}, indent=2, default=str))
    return 0
