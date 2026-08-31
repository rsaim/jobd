"""Run the classification eval and print its scorecard.

    .venv/bin/python evals/run.py --model openrouter/deepseek/deepseek-v4-flash
    .venv/bin/python evals/run.py --learning    # two-pass learning-loop eval

Wraps `deepeval.evaluate` programmatically: the classifier runs once per
message while the test cases are built, DeepEval scores every case on the
four metrics, and the corpus scorecard (precision/recall/F1 need pooled
confusion counts, which per-case averages can't express) prints at the end
— including one slice per removed hardcoded rule class (see
jobd_classify's docstring). --learning replays the corpus twice against
the production learning policy and asserts cost convergence at no
accuracy loss (see learning.py). Pass --verbose for DeepEval's own
per-case report.
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
        help="LiteLLM id for the extractor under eval "
        "(default: JOBD_MODEL, then the built-in default).",
    )
    parser.add_argument(
        "--learning",
        action="store_true",
        help="Run the two-pass learning-loop eval instead (see learning.py).",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help="Run the triage eval: baseline vs batch prefiltering (see eval_triage.py).",
    )
    parser.add_argument(
        "--resolution",
        action="store_true",
        help="Run the resolution eval: windowing + duplicate detection, offline "
        "(see resolution.py).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print DeepEval's per-case results, not just the scorecard.",
    )
    args = parser.parse_args()

    if args.learning:
        from learning import run_learning

        raise SystemExit(run_learning(args.model))

    if args.triage:
        from eval_triage import run_triage_eval

        raise SystemExit(run_triage_eval(args.model))

    if args.resolution:
        from resolution import run_resolution

        raise SystemExit(run_resolution())

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

    tier = args.model or os.environ.get("JOBD_MODEL") or "default model"
    print(f"\njobd classification eval — {tier}")
    print(f"samples: {len(cases)}   cases passing all metrics: {passed}")
    for name, value in scorecard(cases).items():
        if name.endswith("_n"):
            print(f"  {name:24} {int(value)}")
        else:
            print(f"  {name:24} {value:.3f}")


if __name__ == "__main__":
    main()
