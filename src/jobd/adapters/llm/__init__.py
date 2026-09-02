"""`LLMProvider` implementations.

Two, and the ordering says something:

    Ollama     local inference over plain HTTP. **No extra dependency.**
    LiteLLM    cloud routing. Needs `pip install 'jobd-ai[llm]'`.

The old rule-based provider (hand-written regexes) was removed when the
hardcoded classification rules went dynamic — extraction is a model's job
now, and the free tier is the *learned* one: sender rules taught by prior
confident extractions resolve repeat senders with no call at all (see
`jobd.domain.learning`). Local inference remains a first-class
configuration via Ollama; I4 (metadata-only prefilter before any model
call) is upheld upstream regardless of provider.
"""

import os
from typing import Any

from jobd.adapters.llm.ollama import OllamaProvider

__all__ = ["DEFAULT_MODEL", "OllamaProvider", "load_provider"]

#: The model used when neither `--model` nor `JOBD_MODEL` says otherwise.
#: A configuration default, not a classification rule — swap freely via
#: `JOBD_MODEL`.
#:
#: Chosen on measured cost-per-call against this workload, which is heavily
#: input-dominated: a real 9,256-call classify run billed $3.87, implying
#: ~6.1k input tokens against a ~120-token JSON verdict. At that ratio the
#: input price is ~96% of the bill and the output price barely registers, so
#: the pick is the cheapest *input* rate that still does strict json_schema.
#:
#: gpt-oss-20b is $0.03/Mtok in ($1.99 per 10k calls) against DeepSeek V4
#: Flash 0731's $0.065 ($4.18) — 2.1x cheaper for the same work — and carries
#: the strongest published structured-output record in the budget tier
#: (~98% valid JSON). The genuinely cheaper ids (granite-4.0-h-micro at
#: $0.017, ling-3.0-flash at $0.021, qwen3.7-flash at $0.030) do **not**
#: advertise `structured_outputs`, and `LiteLLMProvider.extract` sends
#: `"strict": True` — so they are disqualified without a code change, not
#: merely riskier.
#:
#: Its 131k context is far smaller than DeepSeek's 1.3M. That is slack at
#: ~6.1k tokens/call, but it is the constraint to re-check if
#: `_THREAD_BODY_CAP` or the few-shot context ever grows substantially.
#:
#: Changing this also means changing JOBD_PRICE_PER_MTOK_IN/OUT (see
#: docker-compose.yml) — CreditGuard enforces the run budget against those
#: numbers, so a stale pair silently mis-sizes the ceiling.
DEFAULT_MODEL = "openrouter/openai/gpt-oss-20b"


def load_provider(
    name: str,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    credit_guard: object | None = None,
) -> object:
    """Resolve a provider by name. `litellm` is imported lazily and may fail.

    Args:
        name: ``default``/empty (resolves ``JOBD_MODEL``, then
            :data:`DEFAULT_MODEL`), ``ollama``, ``ollama/<model>``, or any
            LiteLLM model id (``anthropic/claude-sonnet-5``).
        max_tokens: Overrides `LiteLLMProvider`'s 2048 default. Ignored for
            `ollama` (no such cap to raise). classify.py's extraction schema
            is small and 2048 always covers it; a company summary's
            `timeline` can run to dozens of bullets on a long
            correspondence, and JSON-schema mode's structural overhead on
            top of that has genuinely truncated a real response mid-string
            (`json.JSONDecodeError: Unterminated string`) — found live, not
            guessed at.
        credit_guard: Optional CreditGuard attached to cloud providers — every
            paid call is gated before and settled after (balance floor + run
            budget). Ignored for `ollama`, which makes no paid call.
    """
    if name in {"", "default"}:
        name = os.environ.get("JOBD_MODEL", "") or DEFAULT_MODEL
    if name in {"rulebased", "none"}:
        raise ValueError(
            "The rule-based extractor was removed — classification rules are "
            "learned now, not hardcoded. Configure a model: set JOBD_MODEL "
            f"(or pass --model); the default is {DEFAULT_MODEL}. For a "
            "fully-local run, use ollama/<model>."
        )
    if name == "ollama" or name.startswith("ollama/"):
        _, _, model = name.partition("/")
        return OllamaProvider(model=model or "llama3.1")

    from jobd.adapters.llm.litellm_provider import LiteLLMProvider

    kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    if credit_guard is not None:
        kwargs["credit_guard"] = credit_guard
    return LiteLLMProvider(model=name, **kwargs)
