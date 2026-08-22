"""Inspect eval of jobd's message classifier against the demo gold set.

Built on Inspect (https://inspect.aisi.org.uk/), the UK AI Safety
Institute's open-source eval framework, so the runs, logs and metrics use a
standard toolchain rather than a homegrown script — `inspect view` renders
every sample's prediction next to its gold label.

What is measured, per message (message-level, deliberately: the thread
cache and carry layers are cost optimizations on top of this — the
classifier's raw quality is the number that has to hold):

- job-relatedness precision/recall/F1 — prediction = "the pipeline did NOT
  auto-drop it". A false negative here is real job mail silently discarded,
  the one unrecoverable error; a false positive is junk sent onward.
- resolution rate — how much the tier settles on its own (`positive` or
  `negative`) vs punts to the review queue. Punting is safe but costs a
  human (or a paid model) per message.
- stage accuracy — over gold job-related messages, does the predicted
  stage match (None counts, so over-claiming a stage is penalized).
- company accuracy — over gold job-related messages, normalized name match
  (domain-derived crude names like "Quill-finance" count for
  "Quill Finance"; the entity resolver merges those spellings later).

Solvers:
- free tier (default): metadata prefilter, then the regex extractor —
  zero network, runs anywhere.
- any LiteLLM model: `-T model=openrouter/google/gemini-3.7-flash` runs the
  same messages through the paid extractor for a tier-vs-tier comparison.

Run:  .venv/bin/python evals/run.py            # free tier
      .venv/bin/python evals/run.py --model openrouter/google/gemini-3.7-flash
"""

from __future__ import annotations

import email
from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import Metric, Score, Target, metric, scorer
from inspect_ai.solver import Generate, TaskState, solver

from jobd.adapters.llm.rulebased import RuleBasedProvider
from jobd.domain import prefilter
from jobd.domain.extraction import EXTRACTION_SCHEMA, PROMPT, render_for_model
from jobd.services.demo import demo_messages

from gold_demo import GOLD


def _samples() -> list[Sample]:
    out: list[Sample] = []
    for i, raw in enumerate(demo_messages()):
        msg = email.message_from_bytes(raw.payload)
        gold = GOLD[i]
        body = msg.get_payload()
        rendered = render_for_model(
            sender=msg["From"],
            recipient=msg["To"],
            subject=msg["Subject"],
            body=body,
            date=msg["Date"],
            labels=raw.metadata.get("labels"),
        )
        out.append(
            Sample(
                id=f"demo-{i:04d}",
                input=rendered,
                target="job-related" if gold.job_related else "not-job-related",
                metadata={
                    "sender": msg["From"],
                    "labels": raw.metadata.get("labels", ""),
                    "is_bulk": bool(msg["List-Unsubscribe"]),
                    "gold_job_related": gold.job_related,
                    "gold_stage": gold.stage,
                    "gold_company": gold.company,
                },
            )
        )
    return out


@solver
def classify_free_tier(model: str | None = None):
    """The pipeline's own decision procedure, message-level: the metadata
    prefilter answers first (free, and the only layer that sees bulk
    markers); what it can't settle goes to the extractor — the regex
    provider by default, or a LiteLLM model when `model` is passed."""
    extractor: Any = (
        RuleBasedProvider()
        if model is None
        else __import__(
            "jobd.adapters.llm", fromlist=["load_provider"]
        ).load_provider(model)
    )

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        meta = state.metadata
        verdict = prefilter.score(
            sender=meta["sender"],
            recipients="",
            learned=None,
            gmail_labels=meta["labels"] or "",
            has_list_unsubscribe=meta["is_bulk"],
        )
        if verdict.status == "negative":
            pred = {"label": "negative", "via": "prefilter"}
        else:
            payload = extractor.extract(
                PROMPT, EXTRACTION_SCHEMA, text=state.input_text
            )
            pred = {
                "label": payload.get("label"),
                "stage": payload.get("stage"),
                "company_name": payload.get("company_name"),
                "via": extractor.name,
            }
        state.store.set("pred", pred)
        state.output.completion = str(pred)
        return state

    return solve


def _norm(name: str | None) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def _company_match(pred: str | None, gold: str | None) -> bool:
    a, b = _norm(pred), _norm(gold)
    if not a or not b:
        return False
    # Prefix either way: the free extractor derives "Quill-finance" from the
    # domain where the gold truth is "Quill Finance"; both normalize to a
    # shared prefix. The entity resolver merges these spellings downstream.
    return a.startswith(b) or b.startswith(a)


def _value(item: Any) -> dict[str, int]:
    # Inspect has passed metrics list[SampleScore] in some versions and
    # list[Score] in others — read the value either way.
    score = getattr(item, "score", item)
    return score.value  # type: ignore[no-any-return]


def _total(scores: list[Any], key: str) -> int:
    return sum(_value(s).get(key, 0) for s in scores)


def _rate(scores: list[Any], num: str, den: str) -> float:
    d = _total(scores, den)
    return _total(scores, num) / d if d else 0.0


@metric
def precision() -> Metric:
    def compute(scores: list[Any]) -> float:
        tp, fp = _total(scores, "tp"), _total(scores, "fp")
        return tp / (tp + fp) if tp + fp else 0.0

    return compute


@metric
def recall() -> Metric:
    def compute(scores: list[Any]) -> float:
        tp, fn = _total(scores, "tp"), _total(scores, "fn")
        return tp / (tp + fn) if tp + fn else 0.0

    return compute


@metric
def f1() -> Metric:
    def compute(scores: list[Any]) -> float:
        tp = _total(scores, "tp")
        fp, fn = _total(scores, "fp"), _total(scores, "fn")
        return 2 * tp / (2 * tp + fp + fn) if tp else 0.0

    return compute


@metric
def resolution_rate() -> Metric:
    return lambda scores: _rate(scores, "resolved", "n")


@metric
def stage_accuracy() -> Metric:
    return lambda scores: _rate(scores, "stage_ok", "stage_n")


@metric
def company_accuracy() -> Metric:
    return lambda scores: _rate(scores, "company_ok", "company_n")


@scorer(
    metrics=[
        precision(),
        recall(),
        f1(),
        resolution_rate(),
        stage_accuracy(),
        company_accuracy(),
    ]
)
def record_scorer():
    async def score(state: TaskState, target: Target) -> Score:
        pred = state.store.get("pred") or {}
        gold_jr = bool(state.metadata["gold_job_related"])
        pred_jr = pred.get("label") != "negative"
        value: dict[str, int] = {
            "n": 1,
            "tp": int(pred_jr and gold_jr),
            "fp": int(pred_jr and not gold_jr),
            "tn": int(not pred_jr and not gold_jr),
            "fn": int(not pred_jr and gold_jr),
            "resolved": int(pred.get("label") in ("positive", "negative")),
        }
        if gold_jr:
            value["stage_n"] = 1
            value["stage_ok"] = int(
                (pred.get("stage") or None) == state.metadata["gold_stage"]
            )
            value["company_n"] = 1
            value["company_ok"] = int(
                _company_match(
                    pred.get("company_name"), state.metadata["gold_company"]
                )
            )
        return Score(
            value=value,
            answer=str(pred),
            explanation=(
                f"gold: job_related={gold_jr} "
                f"stage={state.metadata['gold_stage']} "
                f"company={state.metadata['gold_company']}"
            ),
        )

    return score


@task
def jobd_classify(model: str | None = None) -> Task:
    return Task(
        dataset=MemoryDataset(_samples()),
        solver=classify_free_tier(model),
        scorer=record_scorer(),
    )
