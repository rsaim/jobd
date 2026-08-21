"""One chat turn: a read-only connection, the tool registry bound to it, and
whatever the provider yields streamed straight back out.

Nowhere in this module is there a write. That is not a convention this file
follows — `SET TRANSACTION READ ONLY` below is what makes it true structurally,
the same guarantee `docs/chat-and-summaries.md` §2/§10 gate 1 describes:
"it does not matter whether a tool was mislabelled ... the database refuses."
"""

from __future__ import annotations

from typing import Any, Iterator

import psycopg

from jobd.ports.chat import Budget, ChatEvent, ChatProvider, Turn
from jobd.tools.registry import build_tools


def converse_turn(
    *,
    conn: psycopg.Connection[Any],
    provider: ChatProvider,
    message: str,
    history: list[Turn] | None = None,
    url_context: str | None = None,
    self_address: str | None = None,
    budget: Budget | None = None,
) -> Iterator[ChatEvent]:
    """Run one turn to completion, yielding events as the provider produces
    them.

    `url_context` folds the current page's URL into the user's own message
    rather than a separate protocol field — M7 gate 1 already guarantees a
    view is fully described by its query parameters, so the model reading
    "[Current page: /company/<id>]" ahead of the question is the entire
    mechanism (docs/chat-and-summaries.md §6).

    `history` is re-sent every turn *without* the message bodies any prior
    tool call retrieved (`ports.chat.Turn` only ever carries names/args/
    summaries for a tool turn) — cost stays linear in conversation length,
    not quadratic (§4).
    """
    budget = budget or Budget()

    # The enforcement gate: issued before `build_tools` exists, so nothing
    # constructed below — however a handler is later mislabelled — ever runs
    # against a connection able to write.
    conn.execute("SET TRANSACTION READ ONLY")

    tools = build_tools(
        conn=conn,
        self_address=self_address,
        max_messages=budget.max_messages,
        max_chars=budget.max_chars_each,
        # `search_communications(mode="semantic")` needs to turn its query
        # text into a vector, and only this turn's own provider can do that
        # — the tool registry has no model access otherwise (every other
        # tool is pure SQL over `conn`). One text in, one vector out; the
        # wrapper only exists so `registry.py` depends on a plain callable,
        # not on `ChatProvider` itself.
        embed=lambda text: provider.embed([text])[0],
    )

    turns = list((history or [])[-budget.max_history :])
    text = f"[Current page: {url_context}]\n\n{message}" if url_context else message
    turns.append(Turn(role="user", text=text))

    yield from provider.converse(turns, tools, budget=budget)
