"""Run the classification eval and print its scorecard.

    .venv/bin/python evals/run.py                       # free tier, offline
    .venv/bin/python evals/run.py --model openrouter/google/gemini-3.7-flash

Wraps `inspect eval` programmatically so the free tier needs no model
flag ceremony (Inspect requires a --model; the solver here never calls
it, so a mock satisfies it). Logs land in evals/logs/ — `inspect view
--log-dir evals/logs` opens the per-sample browser.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from inspect_ai import eval as inspect_eval

from jobd_classify import jobd_classify


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=None,
        help="LiteLLM id for the extractor under eval (default: free tier).",
    )
    args = parser.parse_args()

    logs = inspect_eval(
        jobd_classify(model=args.model),
        model="mockllm/model",
        log_dir=str(Path(__file__).parent / "logs"),
        display="plain",
    )
    results = logs[0].results
    assert results is not None
    metrics = results.scores[0].metrics
    tier = args.model or "free tier (prefilter + rules)"
    print(f"\njobd classification eval — {tier}")
    print(f"samples: {results.total_samples}")
    for name in (
        "precision",
        "recall",
        "f1",
        "resolution_rate",
        "stage_accuracy",
        "company_accuracy",
    ):
        print(f"  {name:18} {metrics[name].value:.3f}")


if __name__ == "__main__":
    main()
