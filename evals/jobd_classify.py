"""DeepEval suite for jobd's message classifier, against the demo gold set.

Built on DeepEval (https://deepeval.com/), Confident AI's open-source eval
framework, so runs use a standard toolchain rather than a homegrown script:
each demo message becomes an `LLMTestCase`, each quality dimension is a
deterministic `BaseMetric`, and `deepeval`'s runner handles execution and
per-case reporting.

What is measured, per message (message-level, deliberately: the thread
cache and carry layers are cost optimizations on top of this — the
classifier's raw quality is the number that has to hold):

- job-relatedness — prediction = "the pipeline did NOT auto-drop it". A
  false negative here is real job mail silently discarded, the one
  unrecoverable error; a false positive is junk sent onward. The corpus
  precision/recall/F1 in the scorecard aggregate this metric's confusion
  counts.
- resolution — did the tier settle the message on its own (`positive` or
  `negative`) vs punting to the review queue. Punting is safe but costs a
  human (or a paid model) per message.
- stage accuracy — over gold job-related messages only (skipped
  elsewhere), does the predicted stage match (None counts, so over-claiming
  a stage is penalized).
- company accuracy — over gold job-related messages only, normalized name
  match (domain-derived crude names like "Runwayml" count for "Runway";
  the entity resolver merges those spellings later).

The pipeline under test is the real decision procedure: the metadata
prefilter answers first (free, and the only layer that sees bulk markers);
what it can't settle goes to the extractor — the regex provider by
default, or any LiteLLM model when `model` is passed.

Run:  .venv/bin/python evals/run.py            # free tier, offline
      .venv/bin/python evals/run.py --model openrouter/google/gemini-3.7-flash
"""

from __future__ import annotations

import email
import json
from email import policy
from typing import Any

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from jobd.adapters.llm.rulebased import RuleBasedProvider
from jobd.domain import prefilter
from jobd.domain.extraction import EXTRACTION_SCHEMA, PROMPT, render_for_model
from jobd.services.demo import demo_messages

from gold_demo import GOLD


def build_cases(model: str | None = None) -> list[LLMTestCase]:
    """Run every demo message through the pipeline's decision procedure and
    wrap the (prediction, gold) pair in a test case. The metrics below read
    both from `metadata`, so no metric re-runs the classifier."""
    extractor: Any = (
        RuleBasedProvider()
        if model is None
        else __import__(
            "jobd.adapters.llm", fromlist=["load_provider"]
        ).load_provider(model)
    )
    cases: list[LLMTestCase] = []
    for i, raw in enumerate(demo_messages()):
        # policy.default gives get_content(), which undoes the transfer
        # encoding — the raw payload wraps long paragraphs quoted-printable,
        # splitting words across lines ("decided not =\nto move"), and the
        # real ingest path decodes exactly the same way before classifying.
        msg = email.message_from_bytes(raw.payload, policy=policy.default)
        gold = GOLD[i]
        labels = raw.metadata.get("labels", "")
        rendered = render_for_model(
            sender=msg["From"],
            recipient=msg["To"],
            subject=msg["Subject"],
            body=msg.get_content(),
            date=msg["Date"],
            labels=labels,
        )
        verdict = prefilter.score(
            sender=msg["From"],
            recipients="",
            learned=None,
            gmail_labels=labels,
            has_list_unsubscribe=bool(msg["List-Unsubscribe"]),
        )
        if verdict.status == "negative":
            pred = {"label": "negative", "via": "prefilter"}
        else:
            payload = extractor.extract(PROMPT, EXTRACTION_SCHEMA, text=rendered)
            pred = {
                "label": payload.get("label"),
                "stage": payload.get("stage"),
                "company_name": payload.get("company_name"),
                "via": extractor.name,
            }
        cases.append(
            LLMTestCase(
                name=f"demo-{i:04d}",
                input=rendered,
                actual_output=json.dumps(pred),
                expected_output=json.dumps(
                    {
                        "job_related": gold.job_related,
                        "stage": gold.stage,
                        "company": gold.company,
                    }
                ),
                metadata={
                    "pred": pred,
                    "gold_job_related": gold.job_related,
                    "gold_stage": gold.stage,
                    "gold_company": gold.company,
                },
            )
        )
    return cases


def _norm(name: str | None) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def _company_match(pred: str | None, gold: str | None) -> bool:
    a, b = _norm(pred), _norm(gold)
    if not a or not b:
        return False
    # Prefix either way: the free extractor derives "Runwayml" from the
    # domain where the gold truth is "Runway"; both normalize to a shared
    # prefix. The entity resolver merges these spellings downstream.
    return a.startswith(b) or b.startswith(a)


class _RecordMetric(BaseMetric):
    """Deterministic base: no judge model, binary score, threshold 0.5.
    Subclasses implement `judge(meta) -> (score, reason)` — or set
    `self.skipped` for cases outside their denominator."""

    def __init__(self) -> None:
        self.threshold = 0.5
        self.async_mode = False

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        self.skipped = False
        score, reason = self.judge(test_case.metadata)
        self.score, self.reason = score, reason
        self.success = self.skipped or score >= self.threshold
        return self.score or 0.0

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    def judge(self, meta: dict[str, Any]) -> tuple[float, str]:
        raise NotImplementedError


class JobRelatedness(_RecordMetric):
    """Did the pipeline keep what mattered and drop what didn't?"""

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Job-relatedness"

    def judge(self, meta: dict[str, Any]) -> tuple[float, str]:
        pred_jr = meta["pred"].get("label") != "negative"
        gold_jr = meta["gold_job_related"]
        ok = pred_jr == gold_jr
        return float(ok), (
            f"pipeline {'kept' if pred_jr else 'dropped'}, "
            f"gold says {'job mail' if gold_jr else 'noise'}"
        )


class Resolution(_RecordMetric):
    """Did the tier settle the message without a human?"""

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Resolution"

    def judge(self, meta: dict[str, Any]) -> tuple[float, str]:
        label = meta["pred"].get("label")
        resolved = label in ("positive", "negative")
        return float(resolved), f"label={label}" + (
            "" if resolved else " — punted to the review queue"
        )


class StageAccuracy(_RecordMetric):
    """Over gold job-related messages: exact stage match, None included."""

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Stage accuracy"

    def judge(self, meta: dict[str, Any]) -> tuple[float, str]:
        if not meta["gold_job_related"]:
            self.skipped = True
            return 0.0, "not job-related in gold — stage not scored"
        pred = meta["pred"].get("stage") or None
        gold = meta["gold_stage"]
        return float(pred == gold), f"predicted {pred!r}, gold {gold!r}"


class CompanyAccuracy(_RecordMetric):
    """Over gold job-related messages: normalized prefix name match."""

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Company accuracy"

    def judge(self, meta: dict[str, Any]) -> tuple[float, str]:
        if not meta["gold_job_related"]:
            self.skipped = True
            return 0.0, "not job-related in gold — company not scored"
        pred = meta["pred"].get("company_name")
        gold = meta["gold_company"]
        return float(_company_match(pred, gold)), f"predicted {pred!r}, gold {gold!r}"


def metrics() -> list[BaseMetric]:
    return [JobRelatedness(), Resolution(), StageAccuracy(), CompanyAccuracy()]


def scorecard(cases: list[LLMTestCase]) -> dict[str, float]:
    """Corpus-level numbers DeepEval's per-case averages can't express:
    precision/recall/F1 need the confusion counts pooled across the run."""
    tp = fp = fn = resolved = 0
    stage_n = stage_ok = company_n = company_ok = 0
    for case in cases:
        meta = case.metadata
        pred, gold_jr = meta["pred"], meta["gold_job_related"]
        pred_jr = pred.get("label") != "negative"
        tp += pred_jr and gold_jr
        fp += pred_jr and not gold_jr
        fn += (not pred_jr) and gold_jr
        resolved += pred.get("label") in ("positive", "negative")
        if gold_jr:
            stage_n += 1
            stage_ok += (pred.get("stage") or None) == meta["gold_stage"]
            company_n += 1
            company_ok += _company_match(
                pred.get("company_name"), meta["gold_company"]
            )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        ),
        "resolution_rate": resolved / len(cases) if cases else 0.0,
        "stage_accuracy": stage_ok / stage_n if stage_n else 0.0,
        "company_accuracy": company_ok / company_n if company_n else 0.0,
    }
