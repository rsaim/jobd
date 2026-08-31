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
what it can't settle goes to the model (`--model`, else JOBD_MODEL, else
the built-in default). There is no regex tier any more — the hardcoded
rules were removed in favour of the learned-rule loop, and the scorecard
grew one slice per removed rule class so the model must *earn* what the
rules used to assert:

- automated_sender_recall — recall over gold job-related mail from
  automated senders (`no-reply@`-class): the mail the old ATS_DOMAINS
  shortcut declared positive by fiat.
- noise_accuracy — over gold noise: the fraction the pipeline still drops
  now that BANK_DOMAINS is gone and negatives must be learned or read.
- webmail_company_guard — no company identity minted from a shared
  mailbox provider (the verdict half of the old GENERIC_DOMAINS list);
  also asserted structurally in evals/learning.py's policy checks.
- the learning loop itself is scored in evals/learning.py: a second pass
  over the corpus must cost fewer model calls at no accuracy loss.

Run:  .venv/bin/python evals/run.py --model openrouter/deepseek/deepseek-v4-flash
      .venv/bin/python evals/run.py --learning   # two-pass learning eval
"""

from __future__ import annotations

import email
import json
from email import policy
from typing import Any

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase
from gold_demo import GOLD

from jobd.adapters.llm import load_provider
from jobd.domain import prefilter
from jobd.domain.extraction import EXTRACTION_SCHEMA, PROMPT, render_for_model
from jobd.domain.resolve import is_generic_domain
from jobd.services.demo import demo_messages


def pred_company(payload: dict[str, Any]) -> str | None:
    """The company the *pipeline* would resolve this payload to — mirrors
    `classify._resolve_company`: an explicit name wins; otherwise the
    extracted `company_domain`'s first label stands in (never for a shared
    provider). The old regex extractor performed this fallback internally,
    so scoring the raw `company_name` field alone would penalize a model
    for structuring its answer the way the schema invites — `company_domain`
    filled, `company_name` null when the body never spells the name out
    (talent@databricks.com mail rarely says "Databricks" in prose)."""
    name = payload.get("company_name")
    if name:
        return str(name)
    domain = (payload.get("company_domain") or "").lower().strip()
    if domain and not is_generic_domain(domain):
        return domain.split(".")[0].capitalize()
    return None


def build_cases(model: str | None = None) -> list[LLMTestCase]:
    """Run every demo message through the pipeline's decision procedure and
    wrap the (prediction, gold) pair in a test case. The metrics below read
    both from `metadata`, so no metric re-runs the classifier."""
    extractor: Any = load_provider(model or "default")
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
                "company_name": pred_company(payload),
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
                    "sender": str(msg["From"] or ""),
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


def _sender_domain(sender: str) -> str:
    from email.utils import getaddresses

    addresses = [a for _, a in getaddresses([sender]) if a]
    if not addresses:
        return ""
    return prefilter.domain_of(addresses[0])


def scorecard(cases: list[LLMTestCase]) -> dict[str, float]:
    """Corpus-level numbers DeepEval's per-case averages can't express:
    precision/recall/F1 need the confusion counts pooled across the run.

    The three slice metrics at the end each stand in for one removed
    hardcoded rule class (see the module docstring) — they are the
    regression tripwires that the model keeps earning what the rules used
    to assert. A slice with no members in the corpus scores 1.0 (nothing
    to get wrong) and its `*_n` count says so honestly.
    """
    tp = fp = fn = resolved = 0
    stage_n = stage_ok = company_n = company_ok = 0
    auto_n = auto_ok = noise_n = noise_ok = webmail_n = webmail_ok = 0
    for case in cases:
        meta = case.metadata
        pred, gold_jr = meta["pred"], meta["gold_job_related"]
        sender = meta.get("sender", "")
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
            # Slice: the old ATS_DOMAINS shortcut — automated senders whose
            # job mail used to be declared positive by list membership.
            if prefilter.is_automated(sender):
                auto_n += 1
                auto_ok += pred_jr
            # Slice: the old GENERIC_DOMAINS verdict — a shared-provider
            # sender must never mint the provider itself as the company.
            domain = _sender_domain(sender)
            if domain and is_generic_domain(domain):
                webmail_n += 1
                provider_name = domain.split(".")[0]
                webmail_ok += not _company_match(
                    pred.get("company_name"), provider_name
                )
        else:
            # Slice: the old BANK_DOMAINS list and label sweeps — noise must
            # still be dropped now that negatives are learned, not listed.
            noise_n += 1
            noise_ok += not pred_jr
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
        "automated_sender_recall": auto_ok / auto_n if auto_n else 1.0,
        "automated_sender_n": float(auto_n),
        "noise_accuracy": noise_ok / noise_n if noise_n else 1.0,
        "noise_n": float(noise_n),
        "webmail_company_guard": webmail_ok / webmail_n if webmail_n else 1.0,
        "webmail_n": float(webmail_n),
    }
