"""Gold labels for the synthetic demo mailbox — the eval's ground truth.

One entry per message in `jobd.services.demo._MAILS`, same order, enforced
by a length assertion at import. The dataset is synthetic, so the truth is
known by construction: each entry states whether the message is evidence of
the user's own job search (`job_related`), which pipeline stage it proves
(`stage`, in the vocabulary `_STAGE_RULES` records), and which employer it
belongs to (`company`; agencies resolve to the client when one is named).

Bulk job-board digests are labeled NOT job-related on purpose: they contain
job vocabulary but are broadcast noise, not evidence of the user's process —
exactly the distinction the prefilter exists to draw.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gold:
    job_related: bool
    stage: str | None = None
    company: str | None = None


GOLD: list[Gold] = [
    # 0-7: Acme Robotics — applied → screens → onsite → offer → accepted
    Gold(True, "applied", "Acme Robotics"),
    Gold(True, "recruiter_screen", "Acme Robotics"),
    Gold(True, None, "Acme Robotics"),                # own reply (outbound)
    Gold(True, "technical", "Acme Robotics"),
    Gold(True, "onsite", "Acme Robotics"),
    Gold(True, "offer", "Acme Robotics"),
    Gold(True, "accepted", "Acme Robotics"),          # own acceptance (outbound)
    Gold(True, "accepted", "Acme Robotics"),
    # 8-12: Meridian Analytics — loop → offer → declined
    Gold(True, "applied", "Meridian Analytics"),
    Gold(True, "onsite", "Meridian Analytics"),
    Gold(True, "offer", "Meridian Analytics"),
    Gold(True, "declined", "Meridian Analytics"),     # own decline (outbound)
    Gold(True, "declined", "Meridian Analytics"),
    # 13-16: Quill Finance — three rounds → rejected
    Gold(True, "applied", "Quill Finance"),
    Gold(True, "phone_screen", "Quill Finance"),
    Gold(True, "technical", "Quill Finance"),
    Gold(True, "rejected", "Quill Finance"),
    # 17-18: Nimbus Cloud — applied → rejected
    Gold(True, "applied", "Nimbus Cloud"),
    Gold(True, "rejected", "Nimbus Cloud"),
    # 19-21: Vector Labs — outreach → screen → ghost
    Gold(True, None, "Vector Labs"),
    Gold(True, None, "Vector Labs"),                  # own reply (outbound)
    Gold(True, "recruiter_screen", "Vector Labs"),
    # 22: Orchid Health — applied into the void
    Gold(True, "applied", "Orchid Health"),
    # 23-25: Beacon Search pitching Juniper Grid
    Gold(True, None, "Juniper Grid"),
    Gold(True, None, "Juniper Grid"),                 # own reply (outbound)
    Gold(True, None, "Juniper Grid"),
    # 26-29: Larkspur Systems — ghosted after technical
    Gold(True, "applied", "Larkspur Systems"),
    Gold(True, "phone_screen", "Larkspur Systems"),
    Gold(True, None, "Larkspur Systems"),             # own reply (outbound)
    Gold(True, "technical", "Larkspur Systems"),
    # 30-35: Copper Peak — live loop at onsite
    Gold(True, "applied", "Copper Peak"),
    Gold(True, "recruiter_screen", "Copper Peak"),
    Gold(True, None, "Copper Peak"),                  # own reply (outbound)
    Gold(True, "technical", "Copper Peak"),
    Gold(True, "onsite", "Copper Peak"),
    Gold(True, None, "Copper Peak"),                  # own reply (outbound)
    # 36-38: Halcyon Grid — screen → rejected
    Gold(True, "applied", "Halcyon Grid"),
    Gold(True, "phone_screen", "Halcyon Grid"),
    Gold(True, "rejected", "Halcyon Grid"),
    # 39-41: Tidegate Security — onsite → rejected
    Gold(True, "applied", "Tidegate Security"),
    Gold(True, "onsite", "Tidegate Security"),
    Gold(True, "rejected", "Tidegate Security"),
    # 42-43: Sable Mountain Capital — withdrawn
    Gold(True, "applied", "Sable Mountain Capital"),
    Gold(True, "withdrawn", "Sable Mountain Capital"),
    # 44-45: cold applies
    Gold(True, "applied", "Bluewren Data"),
    Gold(True, "applied", "Fernwood Biotech"),
    # 46: Northgate — unanswered direct outreach
    Gold(True, None, "Northgate Semiconductors"),
    # 47-49: Harbor Talent Partners — agency, two clients
    Gold(True, None, "Harbor Talent Partners"),
    Gold(True, None, "Harbor Talent Partners"),       # own reply (outbound)
    Gold(True, None, "Halcyon Grid"),
    # 50-57: noise
    Gold(False),  # job-board digest (bulk)
    Gold(False),  # newsletter
    Gold(False),  # bank statement
    Gold(False),  # job-board digest (bulk)
    Gold(False),  # webinar invite
    Gold(False),  # password reset
    Gold(False),  # order shipped
    Gold(False),  # meetup newsletter
]


def check_alignment() -> None:
    from jobd.services.demo import _MAILS

    assert len(GOLD) == len(_MAILS), (
        f"gold has {len(GOLD)} entries, demo has {len(_MAILS)} messages — "
        "update evals/gold_demo.py when the demo dataset changes"
    )


check_alignment()
