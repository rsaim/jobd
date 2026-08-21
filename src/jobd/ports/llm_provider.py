"""LLMProvider — vendor-agnostic model access (PRD §6, P2)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """One model backend. Cloud via LiteLLM, or local via Ollama.

    Local is a first-class configuration, not a degraded one (P2), so this port
    exposes nothing a local model cannot do. No vendor-specific knobs leak
    through it.

    Two properties matter for the privacy stance:

    * ``is_local`` lets M5 assert that an Ollama-configured run made no outbound
      call. Gate 5 of M5 is an assertion, not an inspection.
    * Callers pass pre-filtered candidates only. The port cannot enforce that —
      the deterministic pre-filter upstream does (I4).
    """

    @property
    def name(self) -> str:
        """Model id, e.g. ``"anthropic/claude-sonnet-5"`` or ``"ollama/llama3"``."""
        ...

    @property
    def is_local(self) -> bool:
        """True when inference happens on this machine and nothing leaves it."""
        ...

    def extract(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        text: str,
    ) -> dict[str, Any]:
        """Return a structured extraction conforming to ``schema``.

        Strictly structured output (P2). An implementation that cannot honour
        the schema raises rather than returning prose — a half-parsed extraction
        is worse than none, because it enters the record looking valid.
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input, in order."""
        ...
