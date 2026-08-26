"""A synthetic mailbox, so the dashboard works before Gmail does.

`jobd demo` seeds one believable job search — applications, interview
loops, an accepted offer, a declined one, rejections, a ghosting, agency
outreach, and the bulk noise a real inbox drowns in — through the exact
ingest path real mail takes (RFC822 bytes, content-addressed raw storage,
then the row table). Nothing downstream knows it is synthetic: classify,
sweep, the dashboard and the timeline all read it like mail.

The employers are the Forbes AI 50 (2026) — all fifty appear, so the
dashboard reads like a search someone would actually run this year. The
companies are real; nothing else is. Every message, recruiter, role and
event is invented, sender addresses are role aliases that were never
sent from or to, and the two staffing agencies plus the user's own
address live on the reserved ``.example`` TLD. The clock is relative:
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
# Fifteen companies get full arcs; the other thirty-five appear the way most
# of a real pipeline does — an acknowledgement, a rejection, a screen being
# scheduled, or a recruiter's opener that never got an answer.
_MAILS: list[_Mail] = [
    # --- Anthropic: the full happy path, applied through accepted ---------
    _Mail(38, "no-reply@anthropic.com",
          "We received your application - Senior Backend Engineer",
          "Hi, thanks for applying to Anthropic! Your application for "
          "Senior Backend Engineer has been received. We'll be in touch.",
          thread="anthropic-apply"),
    _Mail(35, "maria@anthropic.com",
          "Anthropic - quick intro call?",
          "Hi! I'm Maria, recruiting at Anthropic. Your application stood "
          "out — do you have 30 minutes this week for an intro call about the "
          "Senior Backend Engineer role?",
          thread="anthropic-loop"),
    _Mail(34.5, DEMO_ACCOUNT,
          "Re: Anthropic - quick intro call?",
          "Hi Maria, absolutely — Thursday afternoon works. Looking forward!",
          to="maria@anthropic.com", thread="anthropic-loop",
          reply_to_prev=True),
    _Mail(30, "maria@anthropic.com",
          "Interview confirmed: technical screen - Anthropic",
          "Confirming your 60-minute technical screen with our platform team "
          "on Tuesday. You'll pair on a small design problem.",
          thread="anthropic-loop", reply_to_prev=True),
    _Mail(22, "maria@anthropic.com",
          "Onsite interview - Senior Backend Engineer",
          "Great news - the team would like to invite you to a final round "
          "onsite interview (virtual). Four rounds: coding, systems design, "
          "infra deep-dive, values.",
          thread="anthropic-loop", reply_to_prev=True),
    _Mail(12, "maria@anthropic.com",
          "Offer - Senior Backend Engineer at Anthropic",
          "We are pleased to extend you an offer for the Senior Backend "
          "Engineer position. Your offer letter is attached; base, equity "
          "and start dates are inside. Happy to walk through details.",
          thread="anthropic-offer"),
    _Mail(9, DEMO_ACCOUNT,
          "Re: Offer - Senior Backend Engineer at Anthropic",
          "I have accepted the offer - signed letter attached. See you on "
          "the 1st!",
          to="maria@anthropic.com", thread="anthropic-offer",
          reply_to_prev=True),
    _Mail(8, "hr@anthropic.com",
          "Welcome to the team!",
          "Wonderful news - welcome to the team! Onboarding will follow "
          "with your start date paperwork and benefits enrollment.",
          thread="anthropic-welcome"),

    # --- OpenAI: strong loop, offer declined ------------------------------
    _Mail(45, "recruiting@openai.com",
          "Your application: Staff Engineer, Data Platform",
          "Thanks for applying to OpenAI. We'd like to move "
          "forward — our recruiter will reach out to schedule a screen.",
          thread="openai"),
    _Mail(40, "recruiting@openai.com",
          "Final round onsite - Staff Engineer, Data Platform",
          "Your screen went well. We would like to invite you to a final "
          "round onsite interview: systems design, two coding rounds, and "
          "a bar-raiser.",
          thread="openai", reply_to_prev=True),
    _Mail(20, "recruiting@openai.com",
          "Offer of employment - OpenAI",
          "Congratulations! We are pleased to extend you an offer for Staff "
          "Engineer, Data Platform. Your offer letter is attached.",
          thread="openai-offer"),
    _Mail(15, DEMO_ACCOUNT,
          "Re: Offer of employment - OpenAI",
          "Thank you - this was a hard call, but I am declining the offer "
          "with OpenAI, as I have accepted another role. I really enjoyed "
          "meeting the team.",
          to="recruiting@openai.com", thread="openai-offer",
          reply_to_prev=True),
    _Mail(14, "recruiting@openai.com",
          "Declining your OpenAI offer - confirmation",
          "Sorry to hear you are declining the offer - thank you for "
          "letting us know so quickly, and best of luck in the new role. "
          "Our door stays open.",
          thread="openai-ack"),

    # --- Databricks: three rounds, then a rejection -----------------------
    _Mail(50, "talent@databricks.com",
          "Application received - Backend Engineer, Lakehouse",
          "Thanks for your interest in Databricks. Your application for "
          "Backend Engineer, Lakehouse is in review.",
          thread="databricks"),
    _Mail(44, "talent@databricks.com",
          "Schedule your phone screen - Databricks",
          "We'd like to schedule a 45-minute phone screen with an engineer "
          "on the lakehouse team. Pick a slot via the link.",
          thread="databricks", reply_to_prev=True),
    _Mail(33, "talent@databricks.com",
          "Technical interview confirmed - Databricks",
          "Your technical interview is confirmed for Friday: one hour, "
          "data structures and a storage-flavoured design question.",
          thread="databricks", reply_to_prev=True),
    _Mail(26, "talent@databricks.com",
          "Update on your Databricks application",
          "Thank you for the time you invested with us. After careful "
          "consideration we've decided not to move forward. We were "
          "impressed and encourage you to apply again.",
          thread="databricks-status"),

    # --- Perplexity: applied, fast rejection ------------------------------
    _Mail(28, "no-reply@perplexity.ai",
          "Thanks for applying to Perplexity",
          "Your application for Site Reliability Engineer has been received.",
          thread="perplexity"),
    _Mail(21, "no-reply@perplexity.ai",
          "Your Perplexity application status",
          "After reviewing your background we will not be moving forward "
          "with your candidacy for Site Reliability Engineer at this time.",
          thread="perplexity-status"),

    # --- Mistral AI: screen happened, then silence (the ghost) ------------
    _Mail(32, "hiring@mistral.ai",
          "Mistral AI - ML Infrastructure Engineer",
          "We came across your profile and think you'd be a great fit for "
          "our ML Infrastructure team. Open to a 30-minute chat?",
          thread="mistral"),
    _Mail(31, DEMO_ACCOUNT,
          "Re: Mistral AI - ML Infrastructure Engineer",
          "Sounds interesting — happy to chat. Wednesday morning?",
          to="hiring@mistral.ai", thread="mistral",
          reply_to_prev=True),
    _Mail(29, "hiring@mistral.ai",
          "Recruiter screen confirmed - Mistral AI",
          "Locked in for Wednesday 10:00. Talk soon!",
          thread="mistral", reply_to_prev=True),
    # ...and nothing since: 29 days of silence reads as ghosted.

    # --- Cohere: applied into the void ------------------------------------
    _Mail(18, "careers@cohere.com",
          "Application confirmation - Platform Engineer",
          "Thank you for applying to Cohere. We review every "
          "application and will contact you if there's a match.",
          thread="cohere"),

    # --- Beacon Search: an agency, pitching a client ----------------------
    _Mail(16, "tom@beacon-search.example",
          "Confidential: Staff Platform role at Cursor",
          "I'm Tom at Beacon Search, retained by Cursor (the AI code "
          "editor) for a Staff Platform Engineer. Comp is strong, "
          "fully remote. Interested in hearing more?",
          thread="beacon"),
    _Mail(13, DEMO_ACCOUNT,
          "Re: Confidential: Staff Platform role at Cursor",
          "Hi Tom — potentially. Can you share the tech stack and band?",
          to="tom@beacon-search.example", thread="beacon",
          reply_to_prev=True),
    _Mail(11, "tom@beacon-search.example",
          "Re: Confidential: Staff Platform role at Cursor",
          "Of course — Go and Kubernetes on the platform side, band attached. "
          "Cursor can move fast; shall I set up an intro?",
          thread="beacon", reply_to_prev=True),

    # --- Cognition: ghosted after the technical ---------------------------
    _Mail(58, "recruiting@cognition.ai",
          "Thanks for applying to Cognition",
          "We received your application for Senior Distributed Systems "
          "Engineer and will review it shortly.",
          thread="cognition-apply"),
    _Mail(52, "recruiting@cognition.ai",
          "Phone screen - Cognition",
          "We would like to schedule a phone screen with the infra team "
          "for your Senior Distributed Systems Engineer application.",
          thread="cognition-loop"),
    _Mail(51.5, DEMO_ACCOUNT,
          "Re: Phone screen - Cognition",
          "Great - Monday or Tuesday afternoon both work for me.",
          to="recruiting@cognition.ai", thread="cognition-loop",
          reply_to_prev=True),
    _Mail(46, "scheduler@cognition.ai",
          "Technical interview confirmed - Cognition",
          "Your technical interview is confirmed for Thursday - a coding "
          "exercise on a shared editor, then distributed-systems design.",
          thread="cognition-tech"),
    # ...then nothing: 46 days of silence after a technical round.

    # --- Thinking Machines Lab: a live loop, currently at onsite ----------
    _Mail(41, "no-reply@thinkingmachines.ai",
          "We received your application - Staff Infrastructure Engineer",
          "Thanks for applying to Thinking Machines Lab! Your application "
          "for Staff Infrastructure Engineer has been received.",
          thread="tml-apply"),
    _Mail(37, "jordan@thinkingmachines.ai",
          "Thinking Machines Lab - intro call this week?",
          "Hi, I am Jordan, recruiting at Thinking Machines Lab. Could we "
          "set up an intro call about the Staff Infrastructure Engineer "
          "role?",
          thread="tml-loop"),
    _Mail(36.5, DEMO_ACCOUNT,
          "Re: Thinking Machines Lab - intro call this week?",
          "Hi Jordan - happy to. Friday morning works best.",
          to="jordan@thinkingmachines.ai", thread="tml-loop",
          reply_to_prev=True),
    _Mail(30, "jordan@thinkingmachines.ai",
          "Technical screen confirmed - Thinking Machines Lab",
          "Confirming your technical screen: one hour, infrastructure "
          "coding, with two engineers from the platform group.",
          thread="tml-loop", reply_to_prev=True),
    _Mail(17, "jordan@thinkingmachines.ai",
          "Final round onsite - Thinking Machines Lab",
          "The team was impressed - we would like to invite you to a final "
          "round onsite interview: systems design, debugging, and a "
          "leadership conversation.",
          thread="tml-onsite"),
    _Mail(16.5, DEMO_ACCOUNT,
          "Re: Final round onsite - Thinking Machines Lab",
          "Wonderful news - next Wednesday works. Thanks Jordan!",
          to="jordan@thinkingmachines.ai", thread="tml-onsite",
          reply_to_prev=True),

    # --- ElevenLabs: phone screen, then a rejection -----------------------
    _Mail(55, "talent@elevenlabs.io",
          "Your application to ElevenLabs",
          "Thank you for applying to ElevenLabs. Your application for "
          "Backend Engineer, Audio Platform is in review.",
          thread="elevenlabs-apply"),
    _Mail(49, "talent@elevenlabs.io",
          "Phone screen invitation - ElevenLabs",
          "We would like to schedule a 45-minute phone screen for the "
          "Backend Engineer, Audio Platform position.",
          thread="elevenlabs-loop"),
    _Mail(43, "talent@elevenlabs.io",
          "Your ElevenLabs application - decision",
          "Thank you for speaking with us. After careful review we have "
          "decided to pursue other candidates whose experience more "
          "closely matches the team's needs right now.",
          thread="elevenlabs-status"),

    # --- Cyera: onsite, then rejected after ------------------------------
    _Mail(64, "careers@cyera.io",
          "Application received - Security Platform Engineer",
          "Thanks for applying to Cyera. We received your "
          "application for Security Platform Engineer.",
          thread="cyera-apply"),
    _Mail(50, "careers@cyera.io",
          "Onsite interview - Security Platform Engineer",
          "Strong screen results - we would like to bring you in for an "
          "onsite interview with the detection and response team.",
          thread="cyera-loop"),
    _Mail(44, "careers@cyera.io",
          "Your Cyera interview - outcome",
          "Thank you for the time you spent with the team. Unfortunately "
          "we have decided to move forward with other candidates for this "
          "position. We would be glad to stay in touch.",
          thread="cyera-status"),

    # --- Safe Superintelligence: candidate withdraws ----------------------
    _Mail(61, "talent@ssi.inc",
          "We received your application - Research Engineer",
          "Thank you for applying. Your application for the Research "
          "Engineer role at Safe Superintelligence has been received.",
          thread="ssi-apply"),
    _Mail(47, "talent@ssi.inc",
          "Withdrawal confirmed - Safe Superintelligence",
          "We have received your request to withdraw your application for "
          "the Research Engineer role at Safe Superintelligence. Your "
          "candidacy is now closed - best of luck with your search.",
          thread="ssi-status"),

    # --- Cold applies into the void ---------------------------------------
    _Mail(24, "no-reply@fireworks.ai",
          "Thanks for applying - Inference Platform Engineer",
          "Thanks for applying to Fireworks AI! Your application for "
          "Inference Platform Engineer has been received and is in review.",
          thread="fireworks"),
    _Mail(19, "jobs@abridge.com",
          "Application received - Senior Software Engineer, Clinical Systems",
          "Thank you for applying to Abridge. We received your "
          "application for Senior Software Engineer, Clinical Systems.",
          thread="abridge"),

    # --- SambaNova: direct recruiter outreach, never answered -------------
    _Mail(10, "sam@sambanova.ai",
          "Your infrastructure background - SambaNova",
          "I lead engineering hiring at SambaNova. Your background looks "
          "like a strong match for a Principal Engineer position on our "
          "inference-systems team. Open to a conversation?",
          thread="sambanova"),

    # --- Harbor Talent Partners: a second agency, two clients -------------
    _Mail(9, "priya@harbortalent.example",
          "Two roles worth a look - platform and infra",
          "Hi! Priya from Harbor Talent Partners here. I am retained by two "
          "companies you might like: Thinking Machines Lab (Staff "
          "Infrastructure Engineer) and ElevenLabs (Backend Engineer, "
          "Audio Platform). Would either be interesting?",
          thread="harbor"),
    _Mail(8.5, DEMO_ACCOUNT,
          "Re: Two roles worth a look - platform and infra",
          "Hi Priya - I am already in process with Thinking Machines Lab, "
          "but tell me more about the ElevenLabs role?",
          to="priya@harbortalent.example", thread="harbor",
          reply_to_prev=True),
    _Mail(8, "priya@harbortalent.example",
          "Re: Two roles worth a look - platform and infra",
          "Understood on Thinking Machines Lab! ElevenLabs is scaling "
          "their audio platform - Go, Postgres, k8s. Band attached. Want "
          "an intro to the hiring manager?",
          thread="harbor", reply_to_prev=True),

    # --- Applied, then rejected -------------------------------------------
    _Mail(54, "careers@harvey.ai",
          "We received your application - Backend Engineer",
          "Thanks for applying to Harvey. We received your application "
          "for Backend Engineer, Knowledge Systems.",
          thread="harvey"),
    _Mail(41, "careers@harvey.ai",
          "Your Harvey application - update",
          "Thank you for your interest. After careful consideration we "
          "have decided not to move forward with your application.",
          thread="harvey-status"),
    _Mail(48, "talent@glean.com",
          "Application received - Search Infrastructure Engineer",
          "Thank you for applying to Glean. Your application for Search "
          "Infrastructure Engineer is in review.",
          thread="glean"),
    _Mail(37, "talent@glean.com",
          "Update on your Glean application",
          "We appreciate the time you invested. We have decided to pursue "
          "other candidates for this position.",
          thread="glean-status"),
    _Mail(45, "recruiting@notion.so",
          "Thanks for applying to Notion",
          "Your application for Software Engineer, Data Platform has been "
          "received.",
          thread="notion"),
    _Mail(33, "recruiting@notion.so",
          "Your Notion application status",
          "Unfortunately we will not be proceeding with your candidacy "
          "for Software Engineer, Data Platform.",
          thread="notion-status"),
    _Mail(42, "careers@runwayml.com",
          "Application received - Infrastructure Engineer, Rendering",
          "Thanks for applying to Runway. We received your application "
          "for Infrastructure Engineer, Rendering.",
          thread="runway"),
    _Mail(30.5, "careers@runwayml.com",
          "Your Runway application - decision",
          "Thank you for your patience. We have decided not to move "
          "forward with your application at this time.",
          thread="runway-status"),
    _Mail(39, "talent@together.ai",
          "We received your application - Systems Engineer, Inference",
          "Thanks for applying to Together AI. Your application for "
          "Systems Engineer, Inference has been received.",
          thread="together"),
    _Mail(27, "talent@together.ai",
          "Update on your Together AI application",
          "After careful review we have decided to pursue other "
          "candidates whose experience more closely matches the role.",
          thread="together-status"),
    _Mail(36, "careers@baseten.co",
          "Thanks for applying to Baseten",
          "Your application for Platform Engineer has been received and "
          "is in review.",
          thread="baseten"),
    _Mail(25, "careers@baseten.co",
          "Your Baseten application status",
          "Thank you for your interest in Baseten. We will not be moving "
          "forward with your application for Platform Engineer.",
          thread="baseten-status"),
    _Mail(31.5, "recruiting@sierra.ai",
          "Application received - Software Engineer, Agent Platform",
          "Thanks for applying to Sierra. We received your application "
          "for Software Engineer, Agent Platform.",
          thread="sierra"),
    _Mail(23, "recruiting@sierra.ai",
          "Your Sierra application - update",
          "We appreciate your interest. Unfortunately we have decided to "
          "move forward with other candidates for this position.",
          thread="sierra-status"),
    _Mail(27.5, "jobs@midjourney.com",
          "We received your application - Backend Engineer",
          "Thanks for applying to Midjourney. Your application for "
          "Backend Engineer has been received.",
          thread="midjourney"),
    _Mail(19.5, "jobs@midjourney.com",
          "Your Midjourney application status",
          "After reviewing your background we have decided not to move "
          "forward with your candidacy at this time.",
          thread="midjourney-status"),
    _Mail(25.5, "careers@skild.ai",
          "Application received - Robotics Software Engineer",
          "Thank you for applying to Skild AI. We received your "
          "application for Robotics Software Engineer.",
          thread="skild"),
    _Mail(13.5, "careers@skild.ai",
          "Your Skild AI application - decision",
          "Thank you for your interest. We have decided to pursue other "
          "candidates for the Robotics Software Engineer position.",
          thread="skild-status"),

    # --- Applied, screen being scheduled (live) ---------------------------
    _Mail(20.5, "recruiting@crusoe.ai",
          "We received your application - Software Engineer",
          "Thanks for applying to Crusoe. Your application for Software "
          "Engineer, Data Center Systems has been received.",
          thread="crusoe"),
    _Mail(6, "recruiting@crusoe.ai",
          "Schedule your phone screen - Crusoe",
          "We would like to schedule a 45-minute phone screen for the "
          "Software Engineer, Data Center Systems position.",
          thread="crusoe-loop"),
    _Mail(16.2, "talent@decagon.ai",
          "Thanks for applying to Decagon",
          "Your application for Backend Engineer has been received and is "
          "in review.",
          thread="decagon"),
    _Mail(5, "talent@decagon.ai",
          "Phone screen invitation - Decagon",
          "We would like to schedule a phone screen with an engineer on "
          "the agents team for the Backend Engineer position.",
          thread="decagon-loop"),
    _Mail(14.2, "careers@clay.com",
          "Application received - Platform Engineer",
          "Thanks for applying to Clay. We received your application for "
          "Platform Engineer.",
          thread="clay"),
    _Mail(4, "careers@clay.com",
          "Schedule your phone screen - Clay",
          "We would like to set up a 30-minute phone screen for the "
          "Platform Engineer position. Pick a slot via the link.",
          thread="clay-loop"),
    _Mail(11.2, "careers@physicalintelligence.company",
          "We received your application - Robotics Infrastructure Engineer",
          "Thank you for applying to Physical Intelligence. Your "
          "application for Robotics Infrastructure Engineer has been "
          "received.",
          thread="pi"),
    _Mail(3, "careers@physicalintelligence.company",
          "Phone screen - Physical Intelligence",
          "We would like to schedule a phone screen with the platform "
          "team for the Robotics Infrastructure Engineer position.",
          thread="pi-loop"),

    # --- Applied, recruiter reaching out for an intro call ----------------
    _Mail(22.5, "talent@replit.com",
          "Thanks for applying to Replit",
          "Your application for Infrastructure Engineer has been received.",
          thread="replit"),
    _Mail(7, "talent@replit.com",
          "Replit - intro call?",
          "Your application stood out — do you have 20 minutes this week "
          "for an intro call about the Infrastructure Engineer role?",
          thread="replit-loop"),
    _Mail(18.5, "careers@suno.com",
          "Application received - Backend Engineer, Audio",
          "Thanks for applying to Suno. We received your application for "
          "Backend Engineer, Audio.",
          thread="suno"),
    _Mail(6.5, "careers@suno.com",
          "Suno - intro call this week?",
          "We would love to set up an intro call about the Backend "
          "Engineer, Audio role. Does Thursday work?",
          thread="suno-loop"),
    _Mail(13.2, "recruiting@worldlabs.ai",
          "We received your application - Systems Engineer",
          "Thank you for applying to World Labs. Your application for "
          "Systems Engineer, Spatial Computing has been received.",
          thread="worldlabs"),
    _Mail(2, "recruiting@worldlabs.ai",
          "World Labs - intro call?",
          "Could we set up an intro call about the Systems Engineer, "
          "Spatial Computing role? A calendar link is below.",
          thread="worldlabs-loop"),

    # --- Recruiter openers, never answered --------------------------------
    _Mail(15.2, "recruiting@reflection.ai",
          "Your systems background - Reflection",
          "I lead recruiting at Reflection. Your background looks like a "
          "strong match for a Member of Technical Staff position on our "
          "infrastructure side. Open to a conversation?",
          thread="reflection"),
    _Mail(12.2, "talent@eliseai.com",
          "Senior Backend Engineer - EliseAI",
          "I came across your profile and think you would be a strong "
          "candidate for our Senior Backend Engineer position. Would you "
          "be open to hearing more?",
          thread="eliseai"),
    _Mail(10.5, "hiring@genspark.ai",
          "Infrastructure role at Genspark",
          "We are hiring for an Infrastructure Engineer position at "
          "Genspark and your experience looks relevant. Interested?",
          thread="genspark"),
    _Mail(9.5, "talent@listenlabs.ai",
          "Backend role - Listen Labs",
          "I am recruiting for a Backend Engineer position at Listen "
          "Labs. Your profile looks like a great fit. Open to hearing "
          "more?",
          thread="listenlabs"),
    _Mail(6.2, "recruiting@rogo.ai",
          "Your platform background - Rogo",
          "We are hiring a Platform Engineer at Rogo and your background "
          "caught our eye. Would you be open to a conversation?",
          thread="rogo"),
    _Mail(5.5, "talent@surgehq.ai",
          "Engineering role at Surge AI",
          "I am hiring for a Software Engineer role at Surge AI. Your "
          "distributed-systems experience looks like a strong match. "
          "Interested in hearing more?",
          thread="surge"),
    _Mail(4.5, "talent@mercor.com",
          "A role that matches your profile - Mercor",
          "I am a recruiter at Mercor. One of the labs we work with is "
          "hiring a Staff Engineer and your profile is a strong match. "
          "Open to a conversation?",
          thread="mercor"),

    # --- Cold applies, acknowledgement only -------------------------------
    _Mail(23.5, "careers@lovable.dev",
          "Thanks for applying to Lovable",
          "Your application for Full-Stack Engineer has been received and "
          "is in review.",
          thread="lovable"),
    _Mail(21.5, "no-reply@fal.ai",
          "Application received - Inference Engineer",
          "Thanks for applying to Fal. We received your application for "
          "Inference Engineer.",
          thread="fal"),
    _Mail(19.8, "careers@openevidence.com",
          "We received your application - Backend Engineer",
          "Thank you for applying to OpenEvidence. Your application for "
          "Backend Engineer has been received.",
          thread="openevidence"),
    _Mail(17.5, "talent@chaidiscovery.com",
          "Thanks for applying - Software Engineer, ML Systems",
          "Thanks for applying to Chai Discovery! Your application for "
          "Software Engineer, ML Systems is in review.",
          thread="chai"),
    _Mail(15.5, "careers@synthesia.io",
          "Application received - Backend Engineer, Video Platform",
          "Thank you for applying to Synthesia. We received your "
          "application for Backend Engineer, Video Platform.",
          thread="synthesia"),
    _Mail(14.5, "careers@heygen.com",
          "Thanks for applying to HeyGen",
          "Your application for Platform Engineer has been received.",
          thread="heygen"),
    _Mail(13.8, "careers@blackforestlabs.ai",
          "We received your application - Systems Engineer",
          "Thanks for applying to Black Forest Labs. Your application for "
          "Systems Engineer, Training Infrastructure has been received.",
          thread="bfl"),
    _Mail(12.5, "careers@krea.ai",
          "Application received - Backend Engineer",
          "Thank you for applying to Krea. Your application for Backend "
          "Engineer has been received and is in review.",
          thread="krea"),
    _Mail(11.5, "careers@gamma.app",
          "Thanks for applying to Gamma",
          "We received your application for Full-Stack Engineer. We "
          "review every application carefully.",
          thread="gamma"),
    _Mail(10.2, "talent@legora.com",
          "Application received - Backend Engineer",
          "Thank you for applying to Legora. Your application for "
          "Backend Engineer has been received.",
          thread="legora"),
    _Mail(8.2, "careers@appliedintuition.com",
          "We received your application - Software Engineer, Simulation",
          "Thanks for applying to Applied Intuition. Your application for "
          "Software Engineer, Simulation has been received.",
          thread="appliedintuition"),
    _Mail(7.5, "careers@speak.com",
          "Thanks for applying to Speak",
          "Your application for Backend Engineer has been received and is "
          "in review.",
          thread="speak"),

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
    _Mail(12, "events@devconf.example",
          "Webinar: scaling Postgres past a billion rows",
          "Join our free webinar on partitioning strategies. Register now.",
          bulk=True, labels="INBOX,CATEGORY_PROMOTIONS"),
    _Mail(4, "no-reply@accounts.example",
          "Your password was reset",
          "Your password was successfully reset. If this was not you, "
          "contact support immediately.",
          labels="INBOX,CATEGORY_UPDATES"),
    _Mail(3, "orders@shopfast.example",
          "Your order has shipped",
          "Order #48219 has shipped and will arrive Thursday.",
          bulk=True, labels="INBOX,CATEGORY_UPDATES"),
    _Mail(1.5, "hello@citymeetups.example",
          "This month: systems engineering meetup",
          "Talks on eBPF and columnar storage. RSVP inside. Unsubscribe "
          "any time.",
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
