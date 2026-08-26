"""Run the classification eval and print its scorecard.

    .venv/bin/python evals/run.py                       # free tier, offline
    .venv/bin/python evals/run.py --model openrouter/google/gemini-3.7-flash

Wraps `deepeval.evaluate` programmatically: the classifier runs once per
message while the test cases are built, DeepEval scores every case on the
four metrics, and the corpus scorecard (precision/recall/F1 need pooled
confusion counts, which per-case averages can't express) prints at the end.
Pass --verbose for DeepEval's own per-case report.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Local, deterministic run: no telemetry, no result upload, no cache dir.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "0")

from deepeval import evaluate
from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig

from jobd_classify import build_cases, metrics, scorecard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=None,
        help="LiteLLM id for the extractor under eval (default: free tier).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print DeepEval's per-case results, not just the scorecard.",
    )
    args = parser.parse_args()

    cases = build_cases(model=args.model)
    result = evaluate(
        cases,
        metrics(),
        async_config=AsyncConfig(run_async=False),
        cache_config=CacheConfig(write_cache=False),
        display_config=DisplayConfig(
            show_indicator=False,
            print_results=args.verbose,
            inspect_after_run=False,
        ),
    )
    passed = sum(
        all(m.success for m in r.metrics_data or []) for r in result.test_results
    )

    tier = args.model or "free tier (prefilter + rules)"
    print(f"\njobd classification eval — {tier}")
    print(f"samples: {len(cases)}   cases passing all metrics: {passed}")
    for name, value in scorecard(cases).items():
        print(f"  {name:18} {value:.3f}")


if __name__ == "__main__":
    main()
