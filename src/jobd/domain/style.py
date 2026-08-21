"""House writing rules for every model-generated surface.

Distilled from the vendored avoid-ai-writing skill
(.claude/skills/avoid-ai-writing/SKILL.md, MIT, Conor Bronsdon, v3.25.0),
which is the full catalog of AI-writing tells with rationale and severity
tiers. That file is 800+ lines and written for auditing existing text; these
blocks are the generation-side subset, compressed to what a model can obey
while writing, and small enough to ride along in every prompt. When the
skill is updated or a new tell is flagged by users, edit here and the fix
lands on every surface at once: the company summary, the chat assistant,
the reply composer, and the reasoning fields of the audit prompts.

Three blocks, by surface:
- WRITING_RULES: full prose surfaces (summary, chat, reply composer).
- EMAIL_RULES:   the extra register for text sent as the user's own mail.
- FIELD_RULES:   one compact paragraph for structured prompts whose JSON
  includes a human-read prose field (verification/audit reasoning) — the
  full block would dwarf the actual task instructions there.
"""

from __future__ import annotations

WRITING_RULES = """\
HOW TO WRITE (applies to everything you produce here):
- Plain and specific. State the fact; never announce its significance. No
  "it's worth noting", "importantly", "interestingly", "notably".
- Banned vocabulary, use the plain word instead: delve, leverage (use),
  robust (strong), seamless, pivotal (key), comprehensive (full),
  landscape, journey, testament, streamline, foster, empower, crucial,
  meticulous, game-changer, "best practices", "at its core", "deep dive",
  utilize (use), "in order to" (to), realm, paradigm, embark, holistic,
  actionable, impactful, learnings, showcase, unpack.
- Prefer "is" and "has" over "serves as", "boasts", "features",
  "represents".
- No em dashes. Use a comma, parentheses, or a new sentence.
- No "It's not X, it's Y" pivots (including split across two sentences),
  no rule-of-three padding, no rhetorical questions as transitions.
- No transition scaffolding: "Moreover", "Furthermore", "Additionally",
  "That said", "When it comes to", "In conclusion". Use "and", "but",
  "also", or restructure so the connection is obvious.
- No chatbot voice: never "Great question", "I hope this helps",
  "Certainly", "Let's dive in", "Let's take a look", and never restate
  the question before answering it.
- No generic closers: "the future looks bright", "only time will tell",
  "as things move forward". End on the last concrete point.
- No false breadth: "Whether you're X or Y", "from A to B" spans that
  name no real range. Address the actual reader about the actual thing.
- No vague attributions: "experts believe", "studies show", "recruiters
  agree". Cite the specific message or fact, or state the claim as yours.
- One hedge at most. "Could potentially" and "may eventually" collapse to
  one word or none.
- A number, name, or date from the record beats an adjective. If the
  record lacks the specific, say what's missing; NEVER invent a number,
  name, date, or event that is not in the record.
- Repeat the clearest word instead of cycling synonyms; forced variation
  reads as thesaurus abuse.
- Vary sentence length; short sentences are fine. Ten same-shaped
  sentences in a row read as machine output. But never chop prose into
  staccato fragments for drama.\
"""

#: The extra register for text meant to be sent as the user's own email.
EMAIL_RULES = """\
The reply must sound like a busy professional writing their own mail:
contractions are fine, sentences stay short, one point per paragraph. Never
open with "I hope this email finds you well" or "I am writing to". Never
close with "Please don't hesitate to reach out". Say the thing, then stop.\
"""

#: Compact variant for structured prompts whose JSON output includes a
#: prose field a person will read. One paragraph on purpose: these prompts
#: are dominated by schema and task instructions, and the field is short.
FIELD_RULES = """\
Any free-text field you write is read by a person: plain words, no em \
dashes, no filler ("it's worth noting", "delve", "leverage", "robust", \
"comprehensive", "crucial", "seamless"), no "It's not X, it's Y" pivots, \
at most one hedge, and cite the specific message, name, or date from the \
input rather than an adjective. Never invent a specific that is not there.\
"""
