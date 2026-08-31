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

from jobd.adapters.llm.ollama import OllamaProvider

__all__ = ["DEFAULT_MODEL", "OllamaProvider", "load_provider"]

#: The model used when neither `--model` nor `JOBD_MODEL` says otherwise.
#: A configuration default, not a classification rule: cheap, fast, and
#: good enough for the extraction schema — swap freely via `JOBD_MODEL`.
DEFAULT_MODEL = "openrouter/deepseek/deepseek-v4-flash"


def load_provider(
    name: str,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
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

    kwargs: dict[str, object] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    return LiteLLMProvider(model=name, **kwargs)
