"""`LLMProvider` implementations.

Three, and the ordering says something:

    RuleBased  no model at all. Deterministic, offline, free. The CI default
               and the eval baseline.
    Ollama     local inference over plain HTTP. **No extra dependency.**
    LiteLLM    cloud routing. Needs `pip install 'jobd-ai[llm]'`.

Local works out of the box and cloud requires an extra install, which is the
install order P2's "local is a first-class configuration, not a degraded one"
implies. It also means the default path makes no outbound call at all, so I4
holds for anyone who never opts in.
"""

from jobd.adapters.llm.ollama import OllamaProvider
from jobd.adapters.llm.rulebased import RuleBasedProvider

__all__ = ["OllamaProvider", "RuleBasedProvider", "load_provider"]


def load_provider(
    name: str,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> object:
    """Resolve a provider by name. `litellm` is imported lazily and may fail.

    Args:
        name: ``rulebased``, ``ollama``, ``ollama/<model>``, or any LiteLLM
            model id (``anthropic/claude-sonnet-5``).
        max_tokens: Overrides `LiteLLMProvider`'s 2048 default. Ignored for
            `rulebased`/`ollama` (no such cap to raise). classify.py's
            extraction schema is small and 2048 always covers it; a company
            summary's `timeline` can run to dozens of bullets on a long
            correspondence, and JSON-schema mode's structural overhead on
            top of that has genuinely truncated a real response mid-string
            (`json.JSONDecodeError: Unterminated string`) — found live, not
            guessed at.
    """
    if name in {"rulebased", "none", ""}:
        return RuleBasedProvider()
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
