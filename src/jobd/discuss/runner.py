"""The debate itself: five models, three rounds, votes at the end.

Run with `python -m jobd.discuss.runner` (env: DATABASE_URL,
OPENROUTER_API_KEY). Each panelist is called through OpenRouter's `:online`
variant, which gives the model live web search on its own initiative — the
"tool" is server-side, so every panelist has it with zero client plumbing.

Round shape:
  1  Diagnose — read the brief, critique the current algorithm, propose.
  2  Debate — read everyone's round 1, attack/adopt/refine, with costs.
  3  Verdict — final position, one line `VOTE: <panelist>` for the best
     proposal (self-votes discarded), one line `EXPERIMENT: <one sentence>`
     naming the single cheapest decisive test.
The moderator turn at the end is deterministic: it tallies votes, names the
GOAT, and lists the experiment proposals verbatim for consensus reading.
"""

from __future__ import annotations

import os
import re
import sys
import time

import litellm

from jobd.discuss import store

#: OpenRouter weekly leaderboard top 5, resolved to exact slugs on
#: 2026-08-17. The date matters: this list is a snapshot, re-rank before
#: reusing.
PANEL = [
    ("openrouter/deepseek/deepseek-v4-flash-0731:online", "DeepSeek V4 Flash 0731"),
    ("openrouter/tencent/hy3:online", "Hy3"),
    ("openrouter/openai/gpt-5.6-luna:online", "GPT-5.6 Luna"),
    ("openrouter/deepseek/deepseek-v4-flash:online", "DeepSeek V4 Flash 0423"),
    ("openrouter/z-ai/glm-5.2:online", "GLM 5.2"),
]

BRIEF = """\
You are one of five AI panelists reviewing jobd, a local-first job-search CRM
that builds a structured record (companies, applications, stage events,
contacts) out of a Gmail mailbox. You have live web search — use it when a
claim needs a source. The other panelists are frontier models; you will see
their turns and they will see yours, attributed by name.

THE CURRENT ALGORITHM, honestly stated, with live numbers:

Ingestion: a seed-and-expand Gmail scrape (LangGraph state machine:
plan, list, fetch, classify, harvest loop, residual, report). Measured on
this mailbox: 97% recall of job-related mail at 20% of a full fetch; a
residual direct-mail pool closes it to ~100%. Raw mail is stored
write-once and content-addressed; the whole record re-derives from raw
(idempotent re-runs; a re-run listed 574 ids, fetched 0, in 31s).

Classification, per message, cheapest gate first:
1. Metadata-only prefilter (bulk headers, bank/label heuristics, learned
   sender rules). Negatives never reach a model. 58k messages total.
2. Thread-carry: any message in an already-settled thread inherits the
   company for free.
3. Deterministic extractor for ATS-domain senders (pure regex/templates).
4. Otherwise an LLM extractor (currently z-ai/glm-5.2, ~$1.2/M in,
   $3.7/M out through OpenRouter) fills a JSON schema: label
   positive/negative/unclassified, company name/domain, role, stage.
5. Confident positives auto-teach an 'undecided' sender-domain rule, so the
   next message from that domain skips ahead. Rules learned: hundreds.
6. Ambiguous results land in a human review queue. It currently holds 186
   pending items: 100 with a NULL sender_address (an old ingest gap),
   24 GitHub notification mails, ~30 newsletters, ~10 real recruiter
   pitches. 28,390 items were resolved as rejected over the record's life.
   The human cannot review 186 by hand; that queue is the pain point.

Audit (after the fact): two-tier sweeps — a cheap model flags linked
messages in batches of 30 snippets/call, a stronger judge confirms before
any unlink; relinks require both tiers to name the same target company
(a lone judge hallucinated targets). A company-entity audit with dossiers
of 8 companies/call deleted 46 junk entities (9% of the company list),
with hard guards: merges need domain/name compatibility; a names-a-company
validator rejects descriptor phrases so the extractor cannot re-mint
deleted junk.

Stats layer: interview rounds count only when corroborated (a
recruiter_screen stage needs the user's own outbound mail within -7/+14
days — without that gate the record claimed 582 interviews; gated, 219).

THE AIM, in the owner's words: the least computationally expensive, most
token-economic, AI-first, reusable and reproducible approach, with almost
100% accuracy. The panelist whose proposal wins the final vote is the GOAT.

Ground rules: be concrete and quantitative (name token counts, call counts,
$ estimates). Attack weak proposals. No flattery. Cite sources when you use
the web. Never propose collecting more user data than the mailbox already
holds.\
"""

ROUND_ASKS = {
    1: "Round 1 — DIAGNOSE. In under 400 words: the two weakest points of "
       "the current algorithm, then your single strongest improvement "
       "proposal with estimated cost (calls, tokens, $) and expected "
       "accuracy effect. Give your proposal a short memorable name.",
    2: "Round 2 — DEBATE. In under 350 words: attack or adopt specific "
       "proposals from the other panelists BY NAME, refine your own with "
       "what you learned, and give a sharpened cost estimate. If someone "
       "else's proposal is better than yours, say so plainly.",
    3: "Round 3 — VERDICT. In under 250 words: your final recommendation "
       "stack (ordered, max 3 items). Then exactly two final lines:\n"
       "VOTE: <panelist display name whose proposal should win — not "
       "yourself>\nEXPERIMENT: <one sentence naming the single cheapest "
       "decisive experiment to run first>",
}


def _speak(model: str, history: list[dict[str, str]]) -> str:
    for attempt in range(3):
        try:
            resp = litellm.completion(
                model=model,
                messages=history,
                # Generous ceiling with reasoning capped low: several
                # panelists are reasoning models and burn the budget on
                # hidden thinking before any visible content otherwise.
                max_tokens=4000,
                temperature=0.7,
                timeout=240,
                extra_body={"reasoning": {"effort": "low"}},
            )
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content.strip()
        except Exception as exc:  # noqa: BLE001 — retry then surface
            if attempt == 2:
                return f"(no answer: {exc})"
            time.sleep(5 * (attempt + 1))
    return "(no answer)"


def run(run_id: str | None = None) -> None:
    run_id = run_id or time.strftime("run-%Y%m%d-%H%M")
    conn = store.connect()
    seq = 0

    store.add(conn, run_id=run_id, seq=seq, round_no=0, model="moderator",
              display="Moderator", role="moderator", content=BRIEF)
    seq += 1

    transcript: list[tuple[str, str]] = []  # (display, content)

    for round_no in (1, 2, 3):
        ask = ROUND_ASKS[round_no]
        for model, display in PANEL:
            prior = "\n\n".join(
                f"### {who} said:\n{what}" for who, what in transcript
            )
            history = [
                {"role": "system", "content": BRIEF},
                {
                    "role": "user",
                    "content": (
                        (f"THE DISCUSSION SO FAR:\n\n{prior}\n\n---\n\n" if prior else "")
                        + f"You are the panelist called {display}.\n{ask}"
                    ),
                },
            ]
            print(f"[round {round_no}] {display} ...", file=sys.stderr)
            content = _speak(model, history)
            transcript.append((display, content))
            store.add(conn, run_id=run_id, seq=seq, round_no=round_no,
                      model=model, display=display, content=content)
            seq += 1

    # Deterministic moderator: tally round-3 votes, list experiments.
    votes: dict[str, int] = {}
    experiments: list[tuple[str, str]] = []
    displays = {d for _, d in PANEL}
    final = transcript[-len(PANEL):]
    for who, what in final:
        m = re.search(r"^VOTE:\s*(.+)$", what, re.MULTILINE)
        if m:
            named = m.group(1).strip().rstrip(".")
            target = next(
                (d for d in displays
                 if d.lower() in named.lower() or named.lower() in d.lower()),
                None,
            )
            if target and target != who:
                votes[target] = votes.get(target, 0) + 1
        e = re.search(r"^EXPERIMENT:\s*(.+)$", what, re.MULTILINE)
        if e:
            experiments.append((who, e.group(1).strip()))

    if votes:
        top = max(votes.values())
        winners = sorted(d for d, n in votes.items() if n == top)
        verdict = " and ".join(winners) + (
            " share the title" if len(winners) > 1 else " is the GOAT"
        )
    else:
        verdict = "No valid votes were cast"

    lines = [f"VOTES: " + (", ".join(f"{d}: {n}" for d, n in
             sorted(votes.items(), key=lambda kv: -kv[1])) or "none"),
             f"VERDICT: {verdict}.", "", "EXPERIMENTS PROPOSED:"]
    lines += [f"- {who}: {what}" for who, what in experiments]
    store.add(conn, run_id=run_id, seq=seq, round_no=4, model="moderator",
              display="Moderator", role="verdict", content="\n".join(lines),
              meta={"votes": votes})
    print("\n".join(lines))
    conn.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
