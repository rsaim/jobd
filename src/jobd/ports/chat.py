"""ChatProvider — multi-turn, tool-calling model access for the chat panel.

Not `LLMProvider` plus a method. `LLMProvider.extract` is one prompt in, one
structured object out, and its docstring says it "exposes nothing a local
model cannot do" — that is what makes a local, offline configuration
first-class rather than a degraded one (P2). Multi-turn tool-calling is a
different shape of capability, bolting it onto `LLMProvider` would quietly
make every local-only guarantee `extract` gives conditional on which method
got called. A separate port keeps that promise intact and makes explicit,
here, that a `ChatProvider` is a new and different exposure — see
docs/chat-and-summaries.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol


@dataclass(frozen=True, slots=True)
class Turn:
    """One entry in the conversation sent to the model.

    `text` is the whole content for a `user`/`assistant` turn. For a `tool`
    turn it is the result summary handed back to the model — never the raw
    retrieved content a moment ago (see `Budget` below on why: re-sending it
    every turn would make cost grow quadratically in conversation length).
    """

    role: Literal["user", "assistant", "tool"]
    text: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Budget:
    """Bounds enforced by the loop, not by any one tool.

    A per-tool cap is trivially evaded by calling the tool again — a model
    that wants eight `message_detail` calls instead of one `list_communications
    (limit=8)` must hit the same ceiling either way, so these are tracked by
    the turn loop across every tool call in the turn, not inside a handler.
    """

    max_messages: int = 8
    max_chars_each: int = 4_000
    max_tool_rounds: int = 6
    max_history: int = 10
    #: Total individual tool calls across the whole turn, not per round —
    #: `max_tool_rounds` alone doesn't stop a model from putting dozens of
    #: calls in a *single* round (e.g. one message_detail per search hit
    #: instead of one list_communications call). Live-caught: 300+ calls in
    #: one turn from an auto-research prompt, well past 6 rounds.
    max_tool_calls: int = 24


@dataclass(frozen=True, slots=True)
class Proposal:
    """A mutation the model wants, rendered as the page's own form.

    Never executed by the chat path — `fields` becomes a pre-filled copy of
    the exact `<form>` the corresponding page already ships, posting to the
    same existing `endpoint`. The chat's connection cannot write regardless
    (`services/chat.py` runs every tool call inside a read-only transaction);
    this is the second, independent reason nothing here mutates anything
    without a human's own click on a form they can read.
    """

    action: str
    summary: str
    endpoint: str
    fields: dict[str, str] = field(default_factory=dict)
    #: What this would touch, shown so "0 backlog" reads as "nothing pending"
    #: rather than as a broken proposal — same number `learning.preview_fanout`
    #: already computes for the existing `/teach` form.
    blast_radius: str | None = None


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    summary: str


@dataclass(frozen=True, slots=True)
class ProposalEvent:
    proposal: Proposal


@dataclass(frozen=True, slots=True)
class Error:
    message: str


@dataclass(frozen=True, slots=True)
class Done:
    #: "busy" is `stream_company_summary`'s single-flight lock loss — a
    #: second caller found generation already in progress elsewhere and
    #: yielded no text at all, distinct from "error" (something failed)
    #: and from a normal "stop" with an empty response.
    reason: Literal["stop", "budget", "error", "busy"]


ChatEvent = TextDelta | ToolCall | ToolResult | ProposalEvent | Error | Done


class ChatProvider(Protocol):
    """Multi-turn, tool-calling access to one model."""

    @property
    def name(self) -> str: ...

    @property
    def is_local(self) -> bool: ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One embedding vector per input, in order — used to turn a
        `search_communications(mode="semantic")` query into the vector
        `services/dashboard.py`'s `semantic_search_communications` compares
        against `message.embedding`. Same method `LLMProvider` already
        requires (`ports/llm_provider.py`); named here too because chat is
        the only caller that needs it at *query* time rather than at
        classification time."""
        ...

    def converse(
        self,
        messages: list[Turn],
        tools: list[Any],
        *,
        budget: Budget,
        system_prompt: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Run one turn to completion, yielding events as they happen —
        genuinely incrementally (token deltas), not the whole reply as one
        event; a caller re-sending `event.text` accumulated across every
        `TextDelta` gets the same result either way, which is what makes
        this a safe change for `services/chat.py`'s existing caller.

        `tools` is `list[tools.registry.Tool]` — typed `Any` here rather than
        imported, so this port does not depend on `jobd.tools` (the tool
        registry is application wiring; this is the vendor-facing interface).
        A `kind="propose"` tool's handler returns a `Proposal`, never writes;
        a `kind="read"` tool's handler runs against a read-only connection the
        caller supplies via closure — this port sees neither, only the
        `Tool.handler` callable each names.

        `system_prompt` overrides `tools/prompts.py`'s `SYSTEM_PROMPT` —
        `None` (every chat-panel call) keeps that default; a caller
        streaming something that isn't the sidebar assistant (a company
        summary, e.g.) supplies its own instructions instead of inheriting
        a persona built for tool-calling over the record.
        """
        ...
