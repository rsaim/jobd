"""Gold labels for the synthetic demo mailbox — truth by construction.

One entry per message in `jobd.services.demo._MAILS`, same order (demo.py
asserts the lengths match at import). Two consumers, one list: the eval
harness scores extractors against it (`evals/gold_demo.py` re-exports this
module), and the demo's replay extractor answers classification from it, so
`jobd demo` needs no model, no key, and no network — the pipeline it feeds
is still the real one.

Outbound replies carry `stage=None` on purpose: a reply confirming an
interview slot proves availability, not a new stage — the inbound message
that scheduled it already carries the stage. Bulk job-board digests are
labeled NOT job-related on purpose: they contain job vocabulary but are
broadcast noise, not evidence of the user's process — exactly the
distinction the prefilter exists to draw.
"""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gold:
    job_related: bool
    stage: str | None = None
    company: str | None = None


GOLD: list[Gold] = [
    # 0-7: Anthropic — applied → screens → onsite → offer → accepted
    Gold(True, "applied", "Anthropic"),
    Gold(True, "recruiter_screen", "Anthropic"),
    Gold(True, None, "Anthropic"),                    # own reply (outbound)
    Gold(True, "technical", "Anthropic"),
    Gold(True, "onsite", "Anthropic"),
    Gold(True, "offer", "Anthropic"),
    Gold(True, "accepted", "Anthropic"),              # own acceptance (outbound)
    Gold(True, "accepted", "Anthropic"),
    # 8-12: OpenAI — loop → offer → declined
    Gold(True, "applied", "OpenAI"),
    Gold(True, "onsite", "OpenAI"),
    Gold(True, "offer", "OpenAI"),
    Gold(True, "declined", "OpenAI"),                 # own decline (outbound)
    Gold(True, "declined", "OpenAI"),
    # 13-16: Databricks — three rounds → rejected
    Gold(True, "applied", "Databricks"),
    Gold(True, "phone_screen", "Databricks"),
    Gold(True, "technical", "Databricks"),
    Gold(True, "rejected", "Databricks"),
    # 17-18: Perplexity — applied → rejected
    Gold(True, "applied", "Perplexity"),
    Gold(True, "rejected", "Perplexity"),
    # 19-21: Mistral AI — outreach → screen → ghost
    Gold(True, None, "Mistral AI"),
    Gold(True, None, "Mistral AI"),                   # own reply (outbound)
    Gold(True, "recruiter_screen", "Mistral AI"),
    # 22: Cohere — applied into the void
    Gold(True, "applied", "Cohere"),
    # 23-25: Beacon Search pitching Cursor
    Gold(True, None, "Cursor"),
    Gold(True, None, "Cursor"),                       # own reply (outbound)
    Gold(True, None, "Cursor"),
    # 26-29: Cognition — ghosted after technical
    Gold(True, "applied", "Cognition"),
    Gold(True, "phone_screen", "Cognition"),
    Gold(True, None, "Cognition"),                    # own reply (outbound)
    Gold(True, "technical", "Cognition"),
    # 30-35: Thinking Machines Lab — live loop at onsite
    Gold(True, "applied", "Thinking Machines Lab"),
    Gold(True, "recruiter_screen", "Thinking Machines Lab"),
    Gold(True, None, "Thinking Machines Lab"),        # own reply (outbound)
    Gold(True, "technical", "Thinking Machines Lab"),
    Gold(True, "onsite", "Thinking Machines Lab"),
    Gold(True, None, "Thinking Machines Lab"),        # own reply (outbound)
    # 36-38: ElevenLabs — screen → rejected
    Gold(True, "applied", "ElevenLabs"),
    Gold(True, "phone_screen", "ElevenLabs"),
    Gold(True, "rejected", "ElevenLabs"),
    # 39-41: Cyera — onsite → rejected
    Gold(True, "applied", "Cyera"),
    Gold(True, "onsite", "Cyera"),
    Gold(True, "rejected", "Cyera"),
    # 42-43: Safe Superintelligence — withdrawn
    Gold(True, "applied", "Safe Superintelligence"),
    Gold(True, "withdrawn", "Safe Superintelligence"),
    # 44-45: cold applies
    Gold(True, "applied", "Fireworks AI"),
    Gold(True, "applied", "Abridge"),
    # 46: SambaNova — unanswered direct outreach
    Gold(True, None, "SambaNova"),
    # 47-49: Harbor Talent Partners — agency, two clients
    Gold(True, None, "Harbor Talent Partners"),
    Gold(True, None, "Harbor Talent Partners"),       # own reply (outbound)
    Gold(True, None, "ElevenLabs"),
    # 50-67: applied → rejected, nine companies
    Gold(True, "applied", "Harvey"),
    Gold(True, "rejected", "Harvey"),
    Gold(True, "applied", "Glean"),
    Gold(True, "rejected", "Glean"),
    Gold(True, "applied", "Notion"),
    Gold(True, "rejected", "Notion"),
    Gold(True, "applied", "Runway"),
    Gold(True, "rejected", "Runway"),
    Gold(True, "applied", "Together AI"),
    Gold(True, "rejected", "Together AI"),
    Gold(True, "applied", "Baseten"),
    Gold(True, "rejected", "Baseten"),
    Gold(True, "applied", "Sierra"),
    Gold(True, "rejected", "Sierra"),
    Gold(True, "applied", "Midjourney"),
    Gold(True, "rejected", "Midjourney"),
    Gold(True, "applied", "Skild AI"),
    Gold(True, "rejected", "Skild AI"),
    # 68-75: applied → phone screen being scheduled, four companies
    Gold(True, "applied", "Crusoe"),
    Gold(True, "phone_screen", "Crusoe"),
    Gold(True, "applied", "Decagon"),
    Gold(True, "phone_screen", "Decagon"),
    Gold(True, "applied", "Clay"),
    Gold(True, "phone_screen", "Clay"),
    Gold(True, "applied", "Physical Intelligence"),
    Gold(True, "phone_screen", "Physical Intelligence"),
    # 76-81: applied → recruiter intro call, three companies
    Gold(True, "applied", "Replit"),
    Gold(True, "recruiter_screen", "Replit"),
    Gold(True, "applied", "Suno"),
    Gold(True, "recruiter_screen", "Suno"),
    Gold(True, "applied", "World Labs"),
    Gold(True, "recruiter_screen", "World Labs"),
    # 82-88: recruiter openers, never answered
    Gold(True, None, "Reflection"),
    Gold(True, None, "EliseAI"),
    Gold(True, None, "Genspark"),
    Gold(True, None, "Listen Labs"),
    Gold(True, None, "Rogo"),
    Gold(True, None, "Surge AI"),
    Gold(True, None, "Mercor"),
    # 89-100: cold applies, acknowledgement only
    Gold(True, "applied", "Lovable"),
    Gold(True, "applied", "Fal"),
    Gold(True, "applied", "OpenEvidence"),
    Gold(True, "applied", "Chai Discovery"),
    Gold(True, "applied", "Synthesia"),
    Gold(True, "applied", "HeyGen"),
    Gold(True, "applied", "Black Forest Labs"),
    Gold(True, "applied", "Krea"),
    Gold(True, "applied", "Gamma"),
    Gold(True, "applied", "Legora"),
    Gold(True, "applied", "Applied Intuition"),
    Gold(True, "applied", "Speak"),
    # 101-108: noise
    Gold(False),  # job-board digest (bulk)
    Gold(False),  # newsletter
    Gold(False),  # bank statement
    Gold(False),  # job-board digest (bulk)
    Gold(False),  # webinar invite
    Gold(False),  # password reset
    Gold(False),  # order shipped
    Gold(False),  # meetup newsletter
]


def check_alignment(mails_len: int) -> None:
    """Called by demo.py's replay extractor — not at import, so this module
    never touches demo.py and the two can import in either order."""
    assert len(GOLD) == mails_len, (
        f"gold has {len(GOLD)} entries, demo has {mails_len} messages — "
        "update demo_gold.py when the demo dataset changes"
    )
