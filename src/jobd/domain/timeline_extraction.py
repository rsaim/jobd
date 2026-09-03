"""Deriving one application's stage timeline from its whole mail chain.

Stage was extracted per message, and that is structurally the wrong unit. A
single "Confirming Zoom Conversation" is genuinely ambiguous read alone --
recruiter screen, technical round and onsite all fit -- and only the sequence
disambiguates it. The corpus showed what that costs: 833 stage events across
229 applications (one carrying 49 of them), `accepted` asserted 67 times
across the 13 applications that have one, 29% of stage evidence being
scheduling logistics (invites, updates, reminders, confirmations, each
independently guessed at), and 82% of `offer` events carrying no offer
language at all -- they were interview invitations.

So the model is asked once per *application*, over the ordered chain, and it
answers about the process rather than about a message. Two properties fall
out that per-message extraction could not have:

* **Coherence.** One call sees "applied" before "onsite" before "offer", so
  it cannot emit a timeline that walks backwards through the funnel.
* **Cost.** ~230 applications against 6,101 per-message extractions, and the
  input is a compact subject/date/sender list rather than message bodies.

The chain is rendered as metadata only -- subject, date, direction, sender.
Bodies never leave the machine on this path (I4's spirit: send the least that
answers the question), and the live evidence is that subjects carry the stage
signal anyway ("Coding Video Interview", "Offer Letter", "Next Steps").

Every claim still links to its evidence: the model names the 1-based index of
the message that proves each stage, and the caller maps it back to a real
message id before writing a `StageEvent` (G2 -- no claim without evidence).
"""

from __future__ import annotations

import re
from typing import Any, get_args

from jobd.domain.record import Stage

#: The stage vocabulary, read off the `Stage` Literal so this enum and the
#: DB's `stage_event_stage_check` can never disagree.
STAGES: tuple[str, ...] = get_args(Stage)

#: One entity's verdict plus its timeline. `related` is the entity-level
#: job-relatedness question: per-message labels answer "is this mail about a
#: job", but only the aggregate can answer "is this *company* actually a
#: hiring process I went through" -- a newsletter domain that once mentioned
#: a role looks positive message-by-message and obviously wrong in aggregate.
TIMELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["related", "events"],
    "properties": {
        "related": {
            "type": "boolean",
            "description": "True only if this is the person's own candidacy.",
        },
        "reason": {"type": ["string", "null"]},
        "role_title": {"type": ["string", "null"]},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["stage", "evidence_index"],
                "properties": {
                    "stage": {"type": "string", "enum": list(STAGES)},
                    "evidence_index": {
                        "type": "integer",
                        "description": "1-based index of the proving message.",
                    },
                },
            },
        },
    },
}

TIMELINE_PROMPT = """\
You are reading every email exchanged with ONE company about ONE job \
application, oldest first, and reconstructing the SHORT LIST of milestones \
that process actually passed through.

Each line is: number, date, direction (-> you sent, <- they sent), sender, \
subject. The last few messages also carry a short body snippet on an \
indented line, because that is where an ending is usually stated.

First decide `related`: is this the person's OWN candidacy -- they applied, \
were recruited, or interviewed? Set it false for a company that only ever \
sent newsletters, product or marketing mail, job-board digests, order \
receipts or event invitations, even if a role was mentioned somewhere. \
Judge the chain as a whole, not the most job-sounding single subject. If \
`related` is false, return an empty `events` list.

Then emit the milestones. THE STAGE VOCABULARY, in process order:

- "applied": an application was submitted or acknowledged; a recruiter's \
first contact that has not yet led to a scheduled conversation.
- "recruiter_screen": an intro/screening call with a recruiter or sourcer \
about fit and logistics -- NOT with the engineers who evaluate the work.
- "phone_screen": a first evaluative conversation with the hiring team, \
often a "quick sync", "chat" or "intro" with a hiring manager or engineer.
- "technical": a coding or technical exercise -- a coding interview, a \
take-home, a CoderPad/HackerRank/CodeSignal link, a technical screen.
- "onsite": the final loop -- a multi-slot day, a "virtual onsite", a \
"final round", an interview panel or itinerary of several back-to-back \
sessions.
- "offer": the company extended an actual offer of employment -- an offer \
letter, a compensation figure, a verbal offer, or negotiation about terms. \
An interview is NOT an offer however far along it is; a final round, an \
onsite invitation and a "next steps" note proposing more interviews are \
interview stages, not offers.
- "rejected": the company said no. "withdrawn": the candidate left before \
any decision. "declined": the candidate turned down an offer that WAS \
extended. "accepted": the candidate said yes -- onboarding or new-hire \
paperwork (welcome mail, background check, I-9/W-4, start date, equipment) \
evidences this even if no message says the word.

HOW TO CHOOSE THEM:

1. A milestone is a step of the process, not a message. One interview \
generates an invitation, a calendar update, a reminder and a confirmation, \
and the same interview may be rescheduled twice more -- all of that is ONE \
event. Cite the single message that shows it best and move on.
2. Emit each stage AT MOST ONCE, unless the mail plainly shows a genuinely \
separate second loop (a different role, or a restart months later).
3. Go forward only. Never emit a stage earlier in the vocabulary than one \
you have already emitted.
4. Do not pad. A typical real process is 2-5 events; even a long one rarely \
exceeds 6. If you find yourself emitting an event for most lines in the \
chain, you are describing messages instead of milestones -- stop and keep \
only the distinct steps.
5. Do include the ending if there is one. Endings hide in the body snippets \
at the bottom, not in subjects: "Re: Next Steps" may well BE the rejection. \
Read those snippets before answering.
6. ONBOARDING MEANS THE OFFER WAS ACCEPTED. If the chain contains new-hire \
or employment-administration mail -- "Welcome to <company>", a manager \
introducing themselves, background check, I-9/W-4, payroll or benefits \
enrolment, start date, equipment or badge setup, or work-visa paperwork \
(H-1B, LCA, support letter, immigration attorney) -- then this person was \
hired, and you must emit "accepted". Emit it even though no message says \
"offer" or "accepted": offers are routinely made by phone or e-signature \
and never appear in the mailbox at all, so this administrative mail is the \
ONLY evidence the process succeeded. A chain that ends in onboarding but \
shows only interviews is a wrong answer.
7. CALENDAR REPLIES ARE NOT HIRING EVENTS. A subject beginning "Accepted:", \
"Declined:", "Tentative:" or "Invitation:" is a meeting RSVP -- someone \
answering a calendar invite, often the recruiter, not the candidate \
answering an offer. "Accepted: <name> | <name>" is two people agreeing to \
meet. Never emit "accepted" or "declined" from one: read it as the \
interview it schedules, or skip it. Only employment paperwork of the kind \
in rule 6 shows an offer was accepted.
8. An out-of-office auto-reply and a bare meeting cancellation evidence \
nothing. Skip them. Emit only what the chain shows -- a chain that never \
got past an acknowledgement is just "applied", and an empty list is a valid \
answer.

`evidence_index` is the 1-based number of the message proving that stage, \
from the list as shown.

Return JSON matching the schema. No prose.
"""


#: The timeline call needs a bigger completion cap than the 2048 an
#: extraction gets: it answers about a whole application at once, so a real
#: process returns several event objects and strict json_schema mode's
#: structural overhead sits on top. A 36-message chain truncated at 2048 with
#: `finish_reason='length'`, which reads back as a short timeline that
#: silently drops the ending. Spend stays bounded by the run's CreditGuard
#: budget -- the one ceiling that matters; this only stops a real answer
#: being cut in half.
TIMELINE_MAX_TOKENS = 8192


#: How much body text rides along on the messages that get any (see
#: `render_chain`). Enough for "we've decided not to move forward" or "we'd
#: like to extend an offer" to be legible; far short of a whole email.
_SNIPPET_CHARS = 300

#: How many of the chain's last messages always carry a body snippet.
#: Endings are the one thing subjects cannot show: of 141 terminal events in
#: the live corpus, exactly 4 (3%) had an outcome word anywhere in the
#: subject -- the rest arrive as "Re: Next Steps" with the decision in the
#: body. Metadata alone therefore cannot close an application, and a timeline
#: that never ends is worse than one with a rough middle.
_TAIL_WITH_BODY = 4

#: Subjects that earn a body snippet wherever they sit in the chain, because
#: they mark an outcome that the tail may not contain.
#:
#: The tail heuristic assumes endings come last, and usually they do -- but
#: an accepted offer is followed by *more* mail, not less: onboarding,
#: payroll, and in one live case two months of H-1B/LCA immigration-attorney
#: threads. The "Welcome to <company>" that actually evidences the offer then
#: sits mid-chain with no snippet, and the model reads a process that just
#: stops after a technical screen. That was a real miss against the
#: operator's own account of which offers were real.
#:
#: Deliberately narrow: outcome words only, no interview vocabulary. Matching
#: "interview" here would put a snippet on half the chain and undo the point
#: of a metadata-first rendering.
_OUTCOME_SUBJECT = re.compile(
    r"\b(offer|welcome\s+to|congratulat|onboard|new\s+hire|start\s+date|"
    r"background\s+check|i-9|w-4|payroll|equity|compensation|"
    r"unfortunat|not\s+moving\s+forward|regret|declin|withdraw|"
    r"h-?1b|lca|visa|immigration)\b",
    re.I,
)


def render_chain(messages: list[dict[str, Any]]) -> str:
    """One application's mail as a numbered, dated list, tail-heavy on detail.

    Subjects, dates, senders and direction for the whole chain; a short body
    snippet only on the last few messages. That split is measured, not
    guessed: subjects carry the stage signal for interview rounds
    ("Coding Video Interview", "Onsite Availability"), but 97% of endings are
    invisible from the subject alone, and an application that never closes is
    the worse failure. Everything before the tail stays metadata-only, so
    this sends a small fraction of what per-message extraction did.

    The index a line carries is the `evidence_index` the model cites back, so
    the order here is load-bearing and callers must not re-sort afterwards.
    """
    lines = []
    tail_starts = max(0, len(messages) - _TAIL_WITH_BODY)
    for i, m in enumerate(messages, start=1):
        sent = m.get("sent_at")
        date = sent.date().isoformat() if sent is not None else "unknown-date"
        arrow = "->" if m.get("direction") == "outbound" else "<-"
        sender = (m.get("sender_address") or "?").strip()
        subject = (m.get("subject") or "(no subject)").strip().replace("\n", " ")
        lines.append(f"{i}. {date} {arrow} {sender}: {subject[:160]}")
        if i - 1 >= tail_starts or _OUTCOME_SUBJECT.search(subject):
            body = " ".join((m.get("body_text") or "").split())
            if body:
                lines.append(f"     | {body[:_SNIPPET_CHARS]}")
    return "\n".join(lines)
