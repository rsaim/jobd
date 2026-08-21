"""A recorded group discussion between frontier models about jobd itself.

Five models (the OpenRouter weekly leaderboard's top five at the time of the
run) debate how the classify/triage algorithm and the product design should
be improved, over three structured rounds, with web search available to
every panelist (OpenRouter's `:online` variant). Every turn is persisted to
the `discussion_message` table so the debate is reviewable after the fact,
and a small standalone web UI serves the transcript.

This is deliberately separate from the product: nothing in jobd imports it.
It is an instrument pointed AT the codebase, kept in the repo so the method
is reusable and the transcript reproducible.
"""
