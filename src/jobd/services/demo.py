"""A synthetic mailbox, so the dashboard works before Gmail does.

`jobd demo` seeds one believable job search — applications, interview
loops, an accepted offer, a declined one, rejections, a ghosting, agency
outreach, and the bulk noise a real inbox drowns in — through the exact
ingest path real mail takes (RFC822 bytes, content-addressed raw storage,
then the row table). Nothing downstream knows it is synthetic: classify,
sweep, the dashboard and the timeline all read it like mail.

Every address lives on the reserved ``.example`` TLD, so the dataset can
never name a real employer or reach a real mailbox. The clock is relative:
each message is dated N days before "now", so the dashboard's activity
graph and silence rails look alive whenever the demo is seeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from jobd.domain.raw import RawMessage

DEMO_ACCOUNT = "you@jobd.example"


@dataclass(frozen=True)
class _Mail:
    days_ago: float
    sender: str
    subject: str
    body: str
    to: str = DEMO_ACCOUNT
    thread: str = ""
    labels: str = "INBOX"
    bulk: bool = False
    reply_to_prev: bool = False  # chain In-Reply-To onto the previous message
    extra_headers: dict[str, str] = field(default_factory=dict)


# One search, told in mail. Ordered oldest-first inside each thread so the
# In-Reply-To chaining below works; threads are interleaved by date anyway.
_MAILS: list[_Mail] = [
    # --- Acme Robotics: the full happy path, applied through accepted -----
    _Mail(38, "no-reply@acme-robotics.example",
          "We received your application - Senior Backend Engineer",
          "Hi, thanks for applying to Acme Robotics! Your application for "
          "Senior Backend Engineer has been received. We'll be in touch.",
          thread="acme-apply"),
    _Mail(35, "maria@acme-robotics.example",
          "Acme Robotics - quick intro call?",
          "Hi! I'm Maria, recruiting at Acme Robotics. Your application stood "
          "out — do you have 30 minutes this week for an intro call about the "
          "Senior Backend Engineer role?",
          thread="acme-loop"),
    _Mail(34.5, DEMO_ACCOUNT,
          "Re: Acme Robotics - quick intro call?",
          "Hi Maria, absolutely — Thursday afternoon works. Looking forward!",
          to="maria@acme-robotics.example", thread="acme-loop",
          reply_to_prev=True),
    _Mail(30, "maria@acme-robotics.example",
          "Interview confirmed: technical screen - Acme Robotics",
          "Confirming your 60-minute technical screen with our platform team "
          "on Tuesday. You'll pair on a small design problem.",
          thread="acme-loop", reply_to_prev=True),
    _Mail(22, "maria@acme-robotics.example",
          "Onsite interview - Senior Backend Engineer",
          "Great news - the team would like to invite you to a final round "
          "onsite interview (virtual). Four rounds: coding, systems design, "
          "infra deep-dive, values.",
          thread="acme-loop", reply_to_prev=True),
    _Mail(12, "maria@acme-robotics.example",
          "Offer - Senior Backend Engineer at Acme Robotics",
          "We are pleased to extend you an offer for the Senior Backend "
          "Engineer position. Your offer letter is attached; base, equity "
          "and start dates are inside. Happy to walk through details.",
          thread="acme-offer"),
    _Mail(9, DEMO_ACCOUNT,
          "Re: Offer - Senior Backend Engineer at Acme Robotics",
          "I have accepted the offer - signed letter attached. See you on "
          "the 1st!",
          to="maria@acme-robotics.example", thread="acme-offer",
          reply_to_prev=True),
    _Mail(8, "hr@acme-robotics.example",
          "Welcome to the team!",
          "Wonderful news - welcome to the team! Onboarding will follow "
          "with your start date paperwork and benefits enrollment.",
          thread="acme-welcome"),

    # --- Meridian Analytics: strong loop, offer declined ------------------
    _Mail(45, "recruiting@meridian-analytics.example",
          "Your application: Staff Engineer, Data Platform",
          "Thanks for applying to Meridian Analytics. We'd like to move "
          "forward — our recruiter will reach out to schedule a screen.",
          thread="meridian"),
    _Mail(40, "recruiting@meridian-analytics.example",
          "Final round onsite - Staff Engineer, Data Platform",
          "Your screen went well. We would like to invite you to a final "
          "round onsite interview: systems design, two coding rounds, and "
          "a bar-raiser.",
          thread="meridian", reply_to_prev=True),
    _Mail(20, "recruiting@meridian-analytics.example",
          "Offer of employment - Meridian Analytics",
          "Congratulations! We are pleased to extend you an offer for Staff "
          "Engineer, Data Platform. Your offer letter is attached.",
          thread="meridian-offer"),
    _Mail(15, DEMO_ACCOUNT,
          "Re: Offer of employment - Meridian Analytics",
          "Thank you - this was a hard call, but I am declining the offer "
          "with Meridian Analytics, as I have accepted another role. I "
          "really enjoyed meeting the team.",
          to="recruiting@meridian-analytics.example", thread="meridian-offer",
          reply_to_prev=True),
    _Mail(14, "recruiting@meridian-analytics.example",
          "Declining your Meridian Analytics offer - confirmation",
          "Sorry to hear you are declining the offer - thank you for "
          "letting us know so quickly, and best of luck in the new role. "
          "Our door stays open.",
          thread="meridian-ack"),

    # --- Quill Finance: three rounds, then a rejection --------------------
    _Mail(50, "talent@quill-finance.example",
          "Application received - Backend Engineer, Payments",
          "Thanks for your interest in Quill Finance. Your application for "
          "Backend Engineer, Payments is in review.",
          thread="quill"),
    _Mail(44, "talent@quill-finance.example",
          "Schedule your phone screen - Quill Finance",
          "We'd like to schedule a 45-minute phone screen with an engineer "
          "on the payments team. Pick a slot via the link.",
          thread="quill", reply_to_prev=True),
    _Mail(33, "talent@quill-finance.example",
          "Technical interview confirmed - Quill Finance",
          "Your technical interview is confirmed for Friday: one hour, "
          "data structures and a payments-flavoured design question.",
          thread="quill", reply_to_prev=True),
    _Mail(26, "talent@quill-finance.example",
          "Update on your Quill Finance application",
          "Thank you for the time you invested with us. After careful "
          "consideration we've decided not to move forward. We were "
          "impressed and encourage you to apply again.",
          thread="quill-status"),

    # --- Nimbus Cloud: applied, fast rejection ----------------------------
    _Mail(28, "no-reply@nimbus-cloud.example",
          "Thanks for applying to Nimbus Cloud",
          "Your application for Site Reliability Engineer has been received.",
          thread="nimbus"),
    _Mail(21, "no-reply@nimbus-cloud.example",
          "Your Nimbus Cloud application status",
          "After reviewing your background we will not be moving forward "
          "with your candidacy for Site Reliability Engineer at this time.",
          thread="nimbus-status"),

    # --- Vector Labs: screen happened, then silence (the ghost) -----------
    _Mail(32, "hiring@vector-labs.example",
          "Vector Labs - ML Infrastructure Engineer",
          "We came across your profile and think you'd be a great fit for "
          "our ML Infrastructure team. Open to a 30-minute chat?",
          thread="vector"),
    _Mail(31, DEMO_ACCOUNT,
          "Re: Vector Labs - ML Infrastructure Engineer",
          "Sounds interesting — happy to chat. Wednesday morning?",
          to="hiring@vector-labs.example", thread="vector",
          reply_to_prev=True),
    _Mail(29, "hiring@vector-labs.example",
          "Recruiter screen confirmed - Vector Labs",
          "Locked in for Wednesday 10:00. Talk soon!",
          thread="vector", reply_to_prev=True),
    # ...and nothing since: 29 days of silence reads as ghosted.

    # --- Orchid Health: applied into the void -----------------------------
    _Mail(18, "careers@orchid-health.example",
          "Application confirmation - Platform Engineer",
          "Thank you for applying to Orchid Health. We review every "
          "application and will contact you if there's a match.",
          thread="orchid"),

    # --- Beacon Search: an agency, pitching a client ----------------------
    _Mail(16, "tom@beacon-search.example",
          "Confidential: Staff Platform role at Juniper Grid",
          "I'm Tom at Beacon Search, retained by Juniper Grid (series C, "
          "energy analytics) for a Staff Platform Engineer. Comp is strong, "
          "fully remote. Interested in hearing more?",
          thread="beacon"),
    _Mail(13, DEMO_ACCOUNT,
          "Re: Confidential: Staff Platform role at Juniper Grid",
          "Hi Tom — potentially. Can you share the tech stack and band?",
          to="tom@beacon-search.example", thread="beacon",
          reply_to_prev=True),
    _Mail(11, "tom@beacon-search.example",
          "Re: Confidential: Staff Platform role at Juniper Grid",
          "Of course — Go and Kubernetes on the platform side, band attached. "
          "Juniper Grid can move fast; shall I set up an intro?",
          thread="beacon", reply_to_prev=True),

    # --- Noise: what prefilter and rules exist to keep out ----------------
    _Mail(7, "digest@jobboard.example",
          "14 new jobs matching your profile",
          "Senior Engineer at 14 companies. See all matching jobs. "
          "You are receiving this because you subscribed to job alerts.",
          bulk=True, labels="INBOX,CATEGORY_PROMOTIONS"),
    _Mail(6, "newsletter@techweekly.example",
          "This week in infrastructure - issue #212",
          "The week's best engineering writing, curated. Unsubscribe below.",
          bulk=True, labels="INBOX,CATEGORY_UPDATES"),
    _Mail(5, "alerts@firstbank.example",
          "Your statement is ready",
          "Your monthly statement is now available in online banking.",
          bulk=True, labels="INBOX,CATEGORY_UPDATES"),
    _Mail(2, "digest@jobboard.example",
          "9 new jobs matching your profile",
          "Staff Engineer at 9 companies. See all matching jobs.",
          bulk=True, labels="INBOX,CATEGORY_PROMOTIONS"),
]


def _rfc822(
    mail: _Mail, sent: datetime, msgid: str, parent: str | None, body: str
) -> bytes:
    msg = EmailMessage()
    msg["From"] = mail.sender
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = format_datetime(sent)
    msg["Message-ID"] = msgid
    if parent:
        msg["In-Reply-To"] = parent
        msg["References"] = parent
    if mail.bulk:
        msg["List-Unsubscribe"] = f"<mailto:unsubscribe@{mail.sender.split('@')[1]}>"
        msg["Precedence"] = "bulk"
    for key, value in mail.extra_headers.items():
        msg[key] = value
    msg.set_content(body)
    return bytes(msg)


def demo_messages(now: datetime | None = None) -> list[RawMessage]:
    """The dataset as RawMessages, dated relative to `now`.

    `now` defaults to today at midnight UTC and message ids are fixed, so
    the payload bytes — and therefore the content-addressed storage keys —
    are identical for every seed run on the same day. Ingest dedupes on
    those keys, which is what makes `jobd demo` safe to re-run."""
    now = now or datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[RawMessage] = []
    last_msgid_in_thread: dict[str, str] = {}
    # Real replies quote the conversation below them, and the classifier's
    # thread path leans on exactly that: it reads only a thread's LATEST
    # message on the premise that the history rides along inside it. A
    # synthetic reply without the quote would break that premise and make
    # every threaded arc illegible, so the quote is built the way a mail
    # client builds it.
    last_quote_in_thread: dict[str, tuple[str, datetime, str]] = {}
    for i, mail in enumerate(_MAILS):
        sent = now - timedelta(days=mail.days_ago)
        msgid = f"<demo-{i:04d}@demo.jobd.example>"
        parent = (
            last_msgid_in_thread.get(mail.thread) if mail.reply_to_prev else None
        )
        body = mail.body
        if mail.reply_to_prev and mail.thread in last_quote_in_thread:
            prev_sender, prev_sent, prev_body = last_quote_in_thread[mail.thread]
            quoted = "\n".join("> " + line for line in prev_body.splitlines())
            body = (
                f"{mail.body}\n\nOn {prev_sent:%a, %d %b %Y at %H:%M}, "
                f"{prev_sender} wrote:\n{quoted}"
            )
        payload = _rfc822(mail, sent, msgid, parent, body)
        if mail.thread:
            last_msgid_in_thread[mail.thread] = msgid
            last_quote_in_thread[mail.thread] = (mail.sender, sent, body)
        metadata: dict[str, str] = {"labels": mail.labels}
        if mail.thread:
            metadata["thread_id"] = f"demo-{mail.thread}"
        out.append(
            RawMessage(
                source="gmail",
                external_id=f"demo-{i:04d}",
                account=DEMO_ACCOUNT,
                fetched_at=now,
                payload=payload,
                metadata=metadata,
            )
        )
    return out


def seed_demo(*, storage: Any, messages: Any, conn: Any) -> dict[str, int]:
    """Ingest the synthetic mailbox through the real pipeline. Same-day
    re-seeds are no-ops (stable content hashes); across days the dates in
    the payload shift, so the CLI refuses to seed a database that already
    holds demo rows rather than minting near-duplicates."""
    from jobd.services.ingest import ingest_raw

    result = ingest_raw(
        demo_messages(),
        storage=storage,
        messages=messages,
        channel="email",
        account=DEMO_ACCOUNT,
    )
    conn.commit()
    return {
        "fetched": result.fetched,
        "stored": result.stored,
        "rows": result.rows_inserted,
        "errors": len(result.errors),
    }


__all__ = ["DEMO_ACCOUNT", "demo_messages", "seed_demo"]
