"""What the extractor is asked for, and what it is allowed to return (P2).

The schema lives in the domain, not in the LLM adapter, because it is a promise
about the record — not about a vendor. Swapping Ollama for a cloud model must not
change what a `StageEvent` can say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jobd.domain.record import Stage

#: The whole confidence question, collapsed to three buckets instead of a
#: continuous score (P2). A numeric confidence only ever drove one decision
#: in this pipeline — a single `< 0.75` comparison — and displayed one number
#: on the timeline; every "how sure" gradation the old scale could express
#: below that cut never changed behaviour. `unclassified` **is** the old
#: below-threshold case: "the system noticed something and declined to act
#: on it," unchanged, just spelled as a label a human reads instead of a
#: float they'd have to already know the cutoff for.
Label = ("positive", "negative", "unclassified")

#: JSON Schema handed to the model. Strict, and `additionalProperties: false`,
#: because a model that invents a field is a model whose output nobody parsed.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label"],
    "properties": {
        "label": {"type": "string", "enum": list(Label)},
        "company_name": {"type": ["string", "null"]},
        "company_domain": {"type": ["string", "null"]},
        "company_kind": {"type": ["string", "null"], "enum": ["employer", "agency", None]},
        "role_title": {"type": ["string", "null"]},
        "stage": {
            "type": ["string", "null"],
            "enum": [
                "applied",
                "recruiter_screen",
                "phone_screen",
                "technical",
                "onsite",
                "offer",
                "rejected",
                "withdrawn",
                "accepted",
                "declined",
                None,
            ],
        },
        "contact_name": {"type": ["string", "null"]},
        "contact_email": {"type": ["string", "null"]},
        "occurred_at": {"type": ["string", "null"], "description": "ISO-8601"},
    },
}

PROMPT = """\
You are reading one email from a job seeker's mailbox and extracting facts.

Rules:
- Report only what the message states or plainly implies. Do not infer a stage
  from tone, and do not guess a company from an email domain that is a mail
  provider (gmail.com, outlook.com, icloud.com).
- `label` is your one classification, in three buckets — pick the one that
  fits, and default to "unclassified" whenever you're not sure which of the
  other two is right, since that is the cheaper mistake:
  - "positive": this is genuinely a conversation about *this person's own*
    candidacy or employment — an application, an interview, a recruiter
    outreach, an offer, a rejection — and you're confident enough to let it
    enter the record without a human checking it first.
  - "negative": you're confident this is NOT job-related — a newsletter,
    job-board digest, or marketing, even one that mentions roles. This
    includes an "offer" that has nothing to do with employment: a lease
    renewal ("we have approval to offer you a 12-month term at $X"), an
    insurance/loan/subscription offer, a retail discount — anything where
    accepting or declining changes something other than where this person
    works. Read what is actually being offered, not just the word "offer"
    or "accepted" appearing in the text — live-caught: an apartment lease
    renewal ("Renewal Lease Offer", a property manager's signature block)
    was recorded as a job offer purely from surface wording.
  - "unclassified": genuinely unsure either way, or probably job-related but
    not confident enough in the details below to record it unchecked. Goes
    to a human review queue rather than the record.
- Look for the company name everywhere in the message, not only the prose
  body: the subject line often names it on its own ("Re: Acme Capital
  interview" names the company even if the reply body never repeats it), and
  a career-site URL anywhere in the message — including inside a password
  reset or portal-login email whose visible template text is entirely
  vendor-branded — usually embeds the real employer as a slug or subdomain:
  Workday (`<company>.wdN.myworkdayjobs.com`), Greenhouse
  (`greenhouse.io/<company>`), Lever (`jobs.lever.co/<company>`), iCIMS
  (`<company>.icims.com`), SmartRecruiters
  (`smartrecruiters.com/<company>`), Ashby (`jobs.ashbyhq.com/<company>`).
  Only leave `company_name` null after checking subject and any URLs, not
  just the sentences — but don't invent a company that genuinely isn't
  anywhere in the message either, a candidate-portal notification sent
  through a pure ATS-vendor domain with no employer URL or subject
  reference in it has no company to name, and guessing one would be worse
  than leaving it null.
- `company_name`/`company_domain` name the entity this message is *about* —
  which is not always the sender. A recruiting agency emailing on behalf of
  a client names the client as the company, not itself, whenever the client
  is named anywhere in the message. Only when no client is named at all
  (common — many agency subjects anonymize the employer, e.g. "$5B
  Startup — Backend Engineer") does the agency's own name become the
  company; leaving `company_name` null in that case is the wrong call, since
  it is a real, nameable entity even though it isn't the employer.
- `company_kind` says which of those two you named: "agency" when
  `company_name`/`company_domain` is the recruiting/staffing firm itself
  (self-identifies as a firm placing candidates, says "our client",
  "on behalf of", lists multiple unrelated open roles), "employer" when it
  is the company that would actually issue the paycheck. Null only when
  neither field is set.
- When the message itself states that the named company is a division,
  practice, or acquired brand of a larger one ("X, a Y business", "X, part
  of Y", "Y's X practice/group/consulting arm", a signature block naming
  both) — name the larger, recognizable parent as `company_name`, not the
  sub-brand. Example: an email from "SubCo Labs, an Acme business"
  names "Acme", not "SubCo Labs" or "Acme SubCo" — same employer,
  same HR org, and a job seeker tracking this cares about that, not the
  internal practice name. Only keep the sub-brand's own name when the
  message never states the parent relationship at all — don't infer an
  affiliation that isn't written down.
- `stage` is the stage this message is evidence *of*, and evidence means an
  interaction that actually happened or was concretely, mutually scheduled
  — a specific date/time both sides confirmed, or a call that already took
  place. A recruiter's cold outreach ("would you like to hop on a call
  about this role?", a LinkedIn pitch, a first "here's an opening") is not
  yet evidence of a screen — nobody has actually screened anyone. Use
  "applied" or leave `stage` null for outreach that hasn't been accepted
  and scheduled yet; only record "recruiter_screen"/"phone_screen"/etc.
  once a concrete session is confirmed or has happened. A rejection is
  "rejected". If the message evidences no transition, use null.
- An out-of-office/automatic-reply message (subject or body says
  "Automatic reply", "Out of Office", "I'll be out of the office", or
  similar) and a bare meeting cancellation/reschedule notice ("Canceled:
  <meeting>", with no further text explaining why) are never evidence of
  any stage, "rejected" included — the process merely paused or one
  session moved, which is not the same claim as "the company said no".
  `stage` is null for both, even though the words might otherwise
  pattern-match a rejection. Real misses, live-caught: an employer's OOO
  auto-reply and a bare "Canceled: Interview" notice were both recorded as
  "rejected" against an application that ended in a real accepted offer
  weeks later.
- An onboarding/new-hire-paperwork thread ("Welcome to <company>",
  background check, I-9/W-4, benefits enrollment, start date, badge/laptop
  setup, corporate card, visa/H-1B transfer) is strong evidence an offer was
  already made and accepted, even if *this* message never itself says
  "offer" or "accepted" — record "accepted" rather than leaving `stage`
  null. Real miss, live-caught: a real offer/negotiation never
  appears anywhere in the mailbox (conveyed by phone or DocuSign outside
  email); the only trace it ever happened is a "Re: Welcome to Acme,
  Alex!" thread about H-1B transfer paperwork, and every message in it was
  left stageless because none literally used the word "offer".
- "offer" means the company extended an actual offer of employment — a
  compensation figure, an offer letter, a verbal offer, or a negotiation
  about terms. **An interview is not an offer, however far along it is.** A
  calendar invitation, a CoderPad or HackerRank link, an availability
  request, an interview reminder or confirmation, a "final round" or
  "onsite" invite, and a "next steps" note proposing more interviews are all
  evidence of an *interview stage* — use "phone_screen"/"technical"/"onsite"
  as fits, or null. Real miss, live-caught: 40 of 49 recorded "offer" events
  had no offer language anywhere in them — they were interview invites
  ("Invitation: Coding Video Interview", "You've been invited to join a
  CoderPad session", "Reminder: You have an upcoming interview"). "offer" is
  the most consequential stage in the record — it decides an application's
  outcome — so require explicit offer language rather than inferring it from
  a process looking advanced.
- "rejected", "withdrawn", "declined", and "accepted" are four different
  endings — do not default to "rejected" or "withdrawn" for any of them.
  "rejected" is the company saying no. "withdrawn" is the candidate leaving
  the process before any decision (no offer was ever extended). "declined" is
  the candidate turning down an offer that *was* extended — the process
  succeeded and they said no anyway. "accepted" is the candidate saying yes.
- Never use "ghosted". It is not a stage — it is the absence of a message.

Return JSON matching the schema. No prose.
"""


def render_for_model(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to: str | None = None,
    date: str | None = None,
    labels: str | None = None,
) -> str:
    """The exact text handed to the extractor.

    Shared by the pipeline and the eval harness, and that is the whole reason
    it exists as a function. They diverged once: the pipeline passed only
    subject and body while the eval passed the headers too, so the eval scored
    an extractor that had the sender's domain — the single strongest company
    signal — and production scored one that did not. The eval read 92% while
    real rejections were being routed to the review queue for lack of a
    company.

    An eval that builds its own input is measuring something the system never
    sees.

    `cc`/`reply_to`/`date` are optional and omitted when empty rather than
    printed blank — most messages don't have a Cc or a distinct Reply-To, and
    padding every prompt with empty header lines buys nothing. When present
    they're real signal a bare From/To/Subject misses: a Cc'd or Reply-To
    address on a different domain than the sender is often the actual
    recruiter/hiring-manager address behind a no-reply or ATS-vendor sender
    (`interviews@modernloop.io` cc'ing someone `@realcompany.com`), and
    `date` grounds "next Tuesday" style relative phrasing the body alone
    can't resolve into `occurred_at`. `labels` is Gmail's own filing, when
    the caller has it (migration 0026 stores it) — same optional-line rule.
    """
    lines = [f"From: {sender}", f"To: {recipient}"]
    if cc:
        lines.append(f"Cc: {cc}")
    if reply_to:
        lines.append(f"Reply-To: {reply_to}")
    if date:
        lines.append(f"Date: {date}")
    if labels:
        # Gmail's own filing (CATEGORY_PROMOTIONS, IMPORTANT, STARRED…) —
        # weak evidence on its own, but the model weighing "PROMOTIONS" next
        # to promotional body text beats it inferring the same thing twice.
        lines.append(f"Gmail-Labels: {labels}")
    lines.append(f"Subject: {subject}")
    return ("\n".join(lines) + f"\n\n{body}").strip()


@dataclass(frozen=True, slots=True)
class Extraction:
    """One model's reading of one message."""

    label: str  # one of Label — "positive" | "negative" | "unclassified"
    company_name: str | None = None
    company_domain: str | None = None
    company_kind: str | None = None  # "employer" | "agency" | None
    role_title: str | None = None
    stage: Stage | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    occurred_at: datetime | None = None

    @property
    def is_positive(self) -> bool:
        """True when this may enter the record directly, pending a company."""
        return self.label == "positive"

    @property
    def is_negative(self) -> bool:
        return self.label == "negative"

    @property
    def names_a_company(self) -> bool:
        """A real, recordable entity — not an anonymized descriptor.

        Recruiter pitches anonymize their client ("YC-Backed AI Startup",
        "Tier 1 Hedge Fund", "a fast-growing startup modernizing…") and a
        model dutifully reports that phrase as `company_name`. A company-level
        audit of a real record found 46 such rows — every one a descriptor
        with no domain. A descriptor without a domain is a description of an
        employer, not an identity for one: the message routes to review
        instead of minting an entity the next audit deletes again.
        """
        if self.company_domain:
            return True
        name = (self.company_name or "").strip()
        if not name:
            return False
        return not _is_descriptor_name(name)


#: Words that mark a "name" as an anonymized description. Sourced from the
#: audit's live catch, not speculation — every deleted junk row matched one.
_DESCRIPTOR_WORDS = frozenset({
    "startup", "startups", "company", "firm", "fund", "unicorn", "stealth",
    "backed", "leading", "growing", "series", "pre-ipo", "scale-up",
    "scaleup", "platform", "vendors", "business", "unknown", "tier",
})


def _is_descriptor_name(name: str) -> bool:
    """True when a company "name" reads as an anonymized descriptor phrase —
    or as no name at all.

    Structural checks first, sourced from a second live batch of junk (the
    rule-based extractor minting "LinkedIn\n\nUpcoming" and bare "This"
    from subject fragments): an embedded newline is never part of a real
    company name, neither is a six-word phrase without a domain, nor a
    single throwaway word.
    """
    if "\n" in name or "\r" in name:
        return True
    words = [w.strip(".,!?()-—:").lower() for w in name.split()]
    words = [w for w in words if w]
    if len(words) > 5:
        return True
    if len(words) == 1 and words[0] in _STOPWORD_NAMES:
        return True
    return bool(set(words) & _DESCRIPTOR_WORDS)


#: Single words that are conversation debris, not identities.
_STOPWORD_NAMES = frozenset({
    "this", "that", "career", "careers", "upcoming", "go", "hi", "hello",
    "on-site", "onsite", "unknown", "team", "jobs", "job", "linkedin",
})


def from_payload(payload: dict[str, Any]) -> Extraction:
    """Build an Extraction from raw model output, coercing defensively.

    Models return `"0.9"` for numbers and `"None"` for nulls often enough that
    strictness here would mean losing real extractions to formatting. What is
    *not* coerced is `stage`: an unknown stage becomes None rather than being
    guessed at, because a wrong stage is a wrong claim in a timeline.
    """
    stage = payload.get("stage")
    if isinstance(stage, str):
        stage = stage.strip().lower() or None
    if stage not in {
        "applied",
        "recruiter_screen",
        "phone_screen",
        "technical",
        "onsite",
        "offer",
        "rejected",
        "withdrawn",
        "accepted",
        "declined",
    }:
        stage = None

    occurred = payload.get("occurred_at")
    parsed_at: datetime | None = None
    if isinstance(occurred, str) and occurred.strip():
        try:
            parsed_at = datetime.fromisoformat(occurred.strip())
        except ValueError:
            parsed_at = None
        # Models write bare dates ("2026-03-04") — offset-naive. Everything
        # this is later compared against (message `sent_at`, timestamptz
        # rows: the stage-monotonicity guard, `applications.close`,
        # `pick_application` windows) is UTC-aware, and a naive-vs-aware
        # comparison raises TypeError — the message then errors out of every
        # batch forever, re-paying the model each time. Pin naive readings
        # to UTC at the one parse site so every consumer sees a comparable
        # datetime.
        if parsed_at is not None and parsed_at.tzinfo is None:
            parsed_at = parsed_at.replace(tzinfo=UTC)

    label = payload.get("label")
    if label not in Label:
        # An invalid/missing label defers rather than guesses — same
        # direction the old `confidence = 0.0` default coercion took (0.0 is
        # always < threshold, i.e. always "review this"), just spelled as
        # the bucket that means exactly that.
        label = "unclassified"

    company_kind = payload.get("company_kind")
    if company_kind not in ("employer", "agency"):
        company_kind = None

    return Extraction(
        label=label,
        company_name=_clean(payload.get("company_name")),
        company_domain=_clean(payload.get("company_domain"), lower=True),
        company_kind=company_kind,
        role_title=_clean(payload.get("role_title")),
        stage=stage,  # type: ignore[arg-type]
        contact_name=_clean(payload.get("contact_name")),
        contact_email=_clean(payload.get("contact_email"), lower=True),
        occurred_at=parsed_at,
    )


def _clean(value: object, *, lower: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "n/a", "unknown"}:
        return None
    return text.lower() if lower else text


# --- Thread-level extraction -------------------------------------------------
#
# The latest email in a thread quotes everything before it, so one reading of
# that single message classifies the whole conversation. The schema is the
# message-level one plus the fields only a whole thread can answer well: the
# agency/client split (an agency thread can pitch several employers) and the
# thread's overall relationship to the job search. `from_payload` reads the
# shared fields off this payload unchanged, so the record path needs no
# second parser.

THREAD_EXTRACTION_SCHEMA: dict[str, Any] = {
    **EXTRACTION_SCHEMA,
    "properties": {
        **EXTRACTION_SCHEMA["properties"],
        #: The recruiting/staffing firm running the thread, when one is —
        #: distinct from company_name, which stays the entity the thread is
        #: about (the client employer when named, the agency itself only
        #: when no client is named anywhere).
        "agency_name": {"type": ["string", "null"]},
        "agency_domain": {"type": ["string", "null"]},
        #: Every employer the thread concretely names as a candidacy target
        #: — an agency thread often pitches more than one. The primary one
        #: (the one applications/stages attach to) still goes in
        #: company_name; this list is the rest, and it may be empty.
        "client_companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": ["string", "null"]},
                    "role_title": {"type": ["string", "null"]},
                },
            },
        },
        "relationship": {
            "type": ["string", "null"],
            "enum": [
                "outreach",
                "application",
                "interview_process",
                "offer",
                "rejection",
                "onboarding",
                "other",
                None,
            ],
        },
    },
}

_THREAD_HEAD = """\
You are reading the LATEST email of one thread from a job seeker's mailbox.
Because email clients quote the conversation below each reply, this one
message usually contains the entire thread — read the quoted history as
evidence too, not just the newest text at the top. Extract facts about the
THREAD as a whole: the label is for the whole conversation, and `stage` is
the furthest stage the thread evidences.

"""

_THREAD_EXTRA_RULES = """\
- `agency_name`/`agency_domain`: when a recruiting/staffing firm is running
  this thread, name it here — always, even when `company_name` holds the
  client employer. When no agency is involved, both stay null.
- `client_companies`: every employer the thread concretely names as a
  candidacy target beyond `company_name` — an agency thread often pitches
  several. Name and, when stated, domain and role for each. Empty list when
  there are none. Never repeat `company_name` in this list.
- `relationship`: what this thread IS in the person's search — "outreach"
  (a recruiter or company reached out, nothing scheduled yet),
  "application" (the person applied, confirmations and portal mail),
  "interview_process" (screens/interviews scheduled or happened),
  "offer", "rejection", "onboarding" (post-acceptance logistics), or
  "other". Null only when not job-related.
"""

THREAD_PROMPT = (
    _THREAD_HEAD + "Rules:" + PROMPT.split("Rules:", 1)[1].rsplit(
        "Return JSON matching the schema. No prose.", 1
    )[0] + _THREAD_EXTRA_RULES + "\nReturn JSON matching the schema. No prose.\n"
)


def render_thread_for_model(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to: str | None = None,
    date: str | None = None,
    labels: str | None = None,
    thread_messages: int | None = None,
    thread_senders: str | None = None,
    first_date: str | None = None,
) -> str:
    """The latest message plus the thread facts the body can't state.

    Everything `render_for_model` sends, with three thread-level header
    lines: how many messages the thread holds, who sent them, and when it
    started — cheap lines that ground "the whole conversation" claims
    without repeating any message content.
    """
    head = render_for_model(
        sender=sender,
        recipient=recipient,
        subject=subject,
        body="",
        cc=cc,
        reply_to=reply_to,
        date=date,
        labels=labels,
    )
    lines = [head]
    if thread_messages:
        lines.append(f"Thread-Messages: {thread_messages}")
    if thread_senders:
        lines.append(f"Thread-Senders: {thread_senders}")
    if first_date:
        lines.append(f"Thread-Started: {first_date}")
    return ("\n".join(lines) + f"\n\n{body}").strip()
