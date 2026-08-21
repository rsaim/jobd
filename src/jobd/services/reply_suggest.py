"""Suggest a reply body to one message — a plain, blocking model call.

Deliberately not streamed like `summaries.stream_company_summary`: a reply
body is a few sentences, not a multi-section briefing, so the UX case for
incremental rendering (a long call the reader watches fill in) does not
apply here the way it does there. One request, one response, the frontend
drops it straight into an editable textarea.

Not cached, unlike the company summary — this is meant to be regenerated on
demand (optionally with the human's own added instructions folded in), so
there is nothing here a fingerprinted cache row would be right to key on.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from jobd.domain.reply_suggest import PROMPT, render_message
from jobd.ports.chat import Budget, TextDelta, Turn
from jobd.ports.llm_provider import LLMProvider
from jobd.services import dashboard


def suggest_reply(
    conn: psycopg.Connection[Any],
    provider: LLMProvider,
    message_id: UUID,
    *,
    extra_instructions: str | None = None,
) -> str | None:
    """The message's own body plus, when given, the human's added
    instructions — both handed to the model as separate, clearly-tagged
    blocks (see `reply_suggest.PROMPT`) so an instruction can't be spoofed
    from inside the quoted email. Returns `None` for a message that does not
    exist or a call that produced no text; the caller turns either into an
    error toast, not a 500."""
    detail = dashboard.message_detail(conn, message_id)
    if detail is None:
        return None

    user_text = render_message(
        subject=detail.subject,
        sender=detail.sender_address,
        body_text=detail.body_text,
    )
    if extra_instructions and extra_instructions.strip():
        user_text += f"\n<instructions>{extra_instructions.strip()}</instructions>"

    converse = getattr(provider, "converse", None)
    if converse is None:
        return None

    text = ""
    for event in converse(
        [Turn(role="user", text=user_text)],
        [],
        budget=Budget(),
        system_prompt=PROMPT,
    ):
        if isinstance(event, TextDelta):
            text += event.text

    return text.strip() or None
