"""Cloud models, routed through LiteLLM (PRD §6).

Behind an extra: `pip install 'jobd-ai[llm]'`. The import is lazy so that a
default install — which is local-only — never pulls it, and so that `jobd
--help` does not pay for a large import on a path that may never use a model.

**This is the disclosure path.** Everything reaching :meth:`extract` leaves the
machine. I4 is upheld upstream, by the deterministic pre-filter deciding what is
even offered here; nothing in this file can restore that property if the caller
skips the filter, which is why the pipeline calls the filter and not the
provider directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from jobd.ports.chat import Budget, ChatEvent, Turn
    from jobd.tools.registry import Tool


class LiteLLMProvider:
    """Any model LiteLLM can route to.

    Args:
        model: A LiteLLM model id, e.g. ``anthropic/claude-sonnet-5``.
        embed_model: Model used for :meth:`embed`.
    """

    def __init__(
        self,
        model: str,
        *,
        # Routed through OpenRouter, like every model id this codebase
        # actually configures (`_CHAT_MODEL_CHOICES` in web/api.py, `--model`
        # in cli/main.py) — the bare OpenAI id needs OPENAI_API_KEY, which
        # nothing here ever sets. Verified live: `openrouter/openai/
        # text-embedding-3-small` returns real 1536-dim vectors (matches the
        # `vector(1536)` column) against just OPENROUTER_API_KEY; the bare
        # id 404s. This is why embedding was never actually usable before —
        # `embed()` existed and was called (`classify --embed`) but every
        # call would have failed against a missing key, which is also why
        # `message.embedding` had zero populated rows.
        embed_model: str = "openrouter/openai/text-embedding-3-small",
        timeout: float = 60.0,
        max_tokens: int = 2048,
        num_retries: int = 0,
        reasoning_effort: str | None = None,
        credit_guard: Any = None,
    ) -> None:
        self._model = model
        self._embed_model = embed_model
        #: Optional CreditGuard. When set, every paid call (`extract`,
        #: `embed`) is gated by `before_call` and settled by `after_call` —
        #: the balance floor and run budget are enforced per-call, not
        #: per-batch. None (local runs, or a caller that wired no guard)
        #: leaves behaviour unchanged.
        self._credit_guard = credit_guard
        #: Cumulative usage across every `extract` call this instance made —
        #: the scrape run's live tokens/cost metrics read these. Plain
        #: counters, no reset: one provider instance is one run's scope.
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0
        self.calls = 0
        # LiteLLM's scalar `timeout=` governs its own request handling and is
        # not sufficient on its own: a response that stalls *mid-body* leaves
        # the process parked in `_ssl__SSLSocket_read` -> `poll` with no
        # deadline to fire, and nothing upstream notices. Live-caught twice on
        # a 461-item derive run -- the process sat with ~6s of CPU across
        # 20 minutes, its Postgres connection `idle in transaction`, waiting
        # on a socket that was never going to answer.
        #
        # A structured timeout is what reaches that read. `litellm.completion`
        # accepts `openai.Timeout`, which *is* `httpx.Timeout`, so the read
        # phase gets its own deadline: connect and write stay short, while
        # `read` -- the gap between response bytes -- is the ceiling that
        # actually fires on a stalled body.
        #
        # The earlier attempt here set `socket.setdefaulttimeout()` instead.
        # That was wrong in a way worth recording: it only applies to sockets
        # created *after* the call, and only when the default is still None,
        # so a pooled connection opened by the HTTP client never inherited it
        # and the same hang recurred.
        self._timeout: Any = timeout
        try:
            import httpx

            self._timeout = httpx.Timeout(
                timeout, connect=min(timeout, 15.0), read=timeout
            )
        except Exception:  # noqa: BLE001 — httpx absent: keep the scalar
            pass
        # Every schema this provider is asked for — EXTRACTION_SCHEMA,
        # VERIFICATION_SCHEMA — is a small, bounded JSON object, but capping
        # too tight backfires: VERIFICATION_SCHEMA's reasoning/
        # misclassification_notes text plus a handful of rule objects can
        # run past 1024 tokens under strict JSON-schema mode's structural
        # overhead, and a response cut off mid-object fails to parse (see
        # `extract`'s finish_reason check below) — that is what actually
        # happened testing against DeepSeek V4 Flash, not a runaway request.
        # Left fully unset, some gateway-routed models (OpenRouter's "auto"
        # router especially) go the other way and default to their full
        # context window — discovered when a sweep asked for 65536 tokens
        # per call and got refused with "can only afford 1358" even though
        # the key's $50 budget was barely touched (OpenRouter checks
        # affordability against the *requested* max_tokens, not actual
        # usage). 2048 is comfortably past every real response seen so far
        # in either direction.
        self._max_tokens = max_tokens
        self._num_retries = num_retries
        #: OpenRouter reasoning mode ("low"/"medium"/"high"), off when None.
        #: Spent where a structural judgment measurably needs it (the app
        #: audit's merge decisions) — not on bulk extraction, where thinking
        #: tokens would multiply the whole mailbox's cost for nothing.
        self._reasoning_effort = reasoning_effort

    @property
    def name(self) -> str:
        return self._model

    @property
    def is_local(self) -> bool:
        """False. Calls made through this provider leave the machine."""
        return False

    def _litellm(self) -> Any:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise ImportError(
                "Cloud models need the optional extra: "
                "pip install 'jobd-ai[llm]'. Or run locally with "
                "`--model ollama/llama3.1`, which needs no extra."
            ) from exc
        return litellm

    def extract(
        self, prompt: str, schema: dict[str, Any], *, text: str
    ) -> dict[str, Any]:
        """Structured extraction via the provider's JSON-schema mode.

        `temperature=0` for the same reason as the local path: a re-derive must
        reproduce the record, or I3's "improve a prompt and re-derive the world"
        turns into "get a different world each time".
        """
        guard = self._credit_guard
        if guard is not None:
            guard.before_call()
        response = self._litellm().completion(
            model=self._model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=self._max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "schema": schema,
                    "strict": True,
                },
            },
            temperature=0,
            timeout=self._timeout,
            # Cheap gateway-routed models (OpenRouter's low-cost tiers
            # especially) rate-limit and gateway-timeout under sequential
            # load routinely — not exceptional, expected. This used to
            # retry 5x with LiteLLM's default exponential backoff, which is
            # what let a single stuck call run past 15 minutes during a
            # verify sweep instead of failing fast. Zero retries: one
            # attempt, one `timeout` ceiling per call — a failed company
            # surfaces immediately as VerifyOutcome.error instead of
            # hanging the whole sweep behind it.
            num_retries=self._num_retries,
            retry_strategy="constant_retry",
            **(
                {"extra_body": {"reasoning": {"effort": self._reasoning_effort}}}
                if self._reasoning_effort
                else {}
            ),
        )
        self.calls += 1
        #: The thinking text behind the last extract, when reasoning mode is
        #: on — read by callers that persist it (thread_extraction.reasoning)
        #: so `jobd distill` can compile the "why" into deterministic rules.
        #: Never re-sent to any model as part of a later prompt.
        choice0 = response.choices[0]
        self.last_reasoning = (
            getattr(choice0.message, "reasoning_content", None)
            or getattr(choice0.message, "reasoning", None)
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        call_cost = 0.0
        try:
            call_cost = float(self._litellm().completion_cost(response) or 0.0)
        except Exception:
            call_cost = 0.0
        if call_cost <= 0.0 and usage is not None:
            # LiteLLM's cost table doesn't cover every gateway-routed model
            # id (openrouter/google/gemini-2.5-flash-lite, and any model newer
            # than the installed litellm — deepseek-v4-flash-0731 among them).
            # `completion_cost` returns 0 for an unknown model (or raises),
            # which would zero the budget charge and let a run spend past its
            # ceiling. Fall back to operator-set $/Mtok knobs so both the
            # dashboard's cost metric and the CreditGuard budget stay honest;
            # without them, tokens still count and cost is a lower bound.
            import os

            try:
                price_in = float(os.environ.get("JOBD_PRICE_PER_MTOK_IN", "0"))
                price_out = float(os.environ.get("JOBD_PRICE_PER_MTOK_OUT", "0"))
            except ValueError:
                price_in = price_out = 0.0
            if price_in or price_out:
                call_cost = (
                    int(getattr(usage, "prompt_tokens", 0) or 0) / 1e6 * price_in
                    + int(getattr(usage, "completion_tokens", 0) or 0)
                    / 1e6
                    * price_out
                )
        self.cost_usd += call_cost
        if guard is not None:
            guard.after_call(call_cost)
        choice = response.choices[0]
        content = choice.message.content
        if not content:
            # A handful of gateway-routed models return an empty/None body
            # instead of raising — a truncated generation, an upstream
            # provider error surfaced as a normal-looking response, or (rare)
            # structured output routed into a tool call instead of message
            # content. `finish_reason` is the one clue the API gives for
            # which; surfacing it beats the bare `TypeError` from handing
            # `None` to `json.loads`, which said nothing about why.
            raise ValueError(
                f"{self._model} returned no content (finish_reason="
                f"{getattr(choice, 'finish_reason', None)!r}) — likely "
                "truncated at max_tokens or an upstream provider error."
            )
        try:
            parsed: dict[str, Any] = json.loads(content)
        except json.JSONDecodeError:
            # Strict json_schema mode still occasionally yields a body that
            # is not JSON (gateway-routed glm does this a few times per
            # hundred calls — observed live in the review sweep and the
            # holdout). One trim-and-salvage attempt: models that fail this
            # way usually wrapped the object in prose or a code fence.
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(content[start : end + 1])
            else:
                raise
        return parsed

    def embed(self, texts: list[str]) -> list[list[float]]:
        guard = self._credit_guard
        if guard is not None:
            guard.before_call()
        response = self._litellm().embedding(model=self._embed_model, input=texts)
        if guard is not None:
            # Embedding cost is not tracked per-call (litellm's embedding
            # response carries no cost on every gateway), so the budget is
            # charged 0 here; the balance floor is still re-checked after.
            guard.after_call(0.0)
        return [list(item["embedding"]) for item in response.data]

    def converse(
        self,
        messages: list[Turn],
        tools: list[Tool],
        *,
        budget: Budget,
        system_prompt: str | None = None,
    ) -> Iterator[ChatEvent]:
        """`ports.chat.ChatProvider.converse` — a real tool-calling loop.

        The model, tool results, and the loop's own bookkeeping never leave
        this method except as `ChatEvent`s. A `kind="propose"` tool's return
        value (a `Proposal`) is never serialized back to the model beyond its
        own summary line — the model is told a proposal was drafted and
        shown, not handed the fields back to reason over further, which
        would just invite it to re-propose variations nobody asked for.

        Genuinely token-streamed only when `tools` is empty. Streaming and
        tool-calling together means accumulating partial function-call JSON
        fragments across chunks before anything is parseable — real
        complexity this file didn't have before and the chat panel's tool
        loop doesn't need solved today. The no-tools path (a company
        summary, e.g.) has no such requirement, so it gets the real thing;
        the tool-calling path keeps the exact behaviour already verified
        against a live server (one `TextDelta` per model turn), unchanged.
        """
        from jobd.ports.chat import (
            Done,
            Error,
            ProposalEvent,
            TextDelta,
            ToolCall,
            ToolResult,
        )

        if system_prompt is None:
            from jobd.tools.prompts import SYSTEM_PROMPT as system_prompt  # noqa: N812

        litellm = self._litellm()
        tool_specs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
        tool_by_name = {t.name: t for t in tools}

        wire: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for m in messages:
            if m.role == "tool":
                continue  # tool turns are not re-sent across turns (§4)
            wire.append({"role": m.role, "content": m.text})

        if not tool_specs:
            try:
                stream = litellm.completion(
                    model=self._model,
                    messages=wire,
                    temperature=0.2,
                    timeout=self._timeout,
                    num_retries=self._num_retries,
                    retry_strategy="constant_retry",
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    text = getattr(delta, "content", None)
                    if text:
                        yield TextDelta(text)
            except Exception as exc:  # a stuck/failed call must not hang the caller
                yield Error(f"{self._model}: {exc}")
                yield Done("error")
                return
            yield Done("stop")
            return

        rounds = 0
        calls_made = 0
        char_budget = budget.max_chars_each * budget.max_messages
        while True:
            try:
                response = litellm.completion(
                    model=self._model,
                    messages=wire,
                    tools=tool_specs or None,
                    tool_choice="auto" if tool_specs else None,
                    temperature=0.2,
                    timeout=self._timeout,
                    num_retries=self._num_retries,
                    retry_strategy="constant_retry",
                )
            except Exception as exc:  # a stuck/failed call must not hang the panel
                yield Error(f"{self._model}: {exc}")
                yield Done("error")
                return

            choice = response.choices[0]
            msg = choice.message
            if msg.content:
                yield TextDelta(msg.content)

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                yield Done("stop")
                return

            if rounds >= budget.max_tool_rounds:
                yield Done("budget")
                return
            rounds += 1

            wire.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                if calls_made >= budget.max_tool_calls:
                    # Stop mid-round, not just between rounds — a round can
                    # carry far more than a handful of calls on its own.
                    yield Done("budget")
                    return
                calls_made += 1
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    wire.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Malformed arguments: {exc}",
                        }
                    )
                    yield ToolResult(name=name, ok=False, summary=f"Malformed arguments: {exc}")
                    continue

                tool = tool_by_name.get(name)
                if tool is None:
                    result_text = f"Unknown tool {name!r}."
                    yield ToolResult(name=name, ok=False, summary=result_text)
                    wire.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
                    continue

                yield ToolCall(name=name, args=args)
                try:
                    result = tool.handler(**args)
                except Exception as exc:
                    # One bad call must not end the turn (§4 failure modes) —
                    # fed back once so the model can recover or explain;
                    # a second failure on the same tool is the model's to
                    # give up on, same as any other tool result it reads.
                    result_text = f"Error calling {name}: {exc}"
                    yield ToolResult(name=name, ok=False, summary=result_text)
                    wire.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
                    continue

                if tool.kind == "propose":
                    yield ProposalEvent(result)
                    result_text = f"Proposal drafted and shown to the user: {result.summary}"
                    yield ToolResult(name=name, ok=True, summary=result.summary)
                else:
                    payload = json.dumps(result, default=str)
                    if len(payload) > char_budget:
                        payload = payload[:char_budget] + "...(truncated, budget hit)"
                    result_text = payload
                    yield ToolResult(name=name, ok=True, summary=f"{name} returned data")
                wire.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
