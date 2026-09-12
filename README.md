# jobd

**Finds 97% of the job-related mail in a Gmail account by fetching 20% of it.
100% at 29%.** No mailbox download, no hardcoded sender lists — a search loop
that learns which senders to ask about next, and gets cheaper every run. The
result is a local-first job-search record built from your own mail: companies,
applications, contacts, stage events, every row linked to the message it came
from, and a dashboard to browse and correct it.

![The Today dashboard on the demo corpus: a company funnel, an interview-day
heatmap, estimated prep and in-room hours, and median weeks to an offer —
every figure derived from the mail itself](docs/img/today-demo.png)

## The problem, measured

The obvious way to build this is to download the mailbox and classify it. On
the reference mailbox that means fetching 58,331 messages to find the 2,631
that mention a company — 4.5% signal. Fetch volume scales with how much mail
you have, not how much of it matters; at 100K+ messages the fetch is the
whole cost.

So the mailbox got labeled, and retrieval strategies got scored against it.
Some of the obvious assumptions turned out to be wrong:

| Query family | Fetches | Recall | Precision |
| --- | ---: | ---: | ---: |
| `careers@` / `talent@` / `recruiting@` local-parts | 3,542 | 0.3% | **0.2%** |
| ATS domains (greenhouse, lever, ashby…) | 158 | 4.2% | 69.6% |
| LinkedIn InMail relay | 528 | 14.4% | 71.8% |
| `in:sent` + the full threads of those messages | 2,081 | **51.0%** | 64.5% |

The `careers@` heuristic — the first thing anyone reaches for — is worthless:
those addresses send marketing, not candidacies. What works is your own sent
mail: half of all job mail sits in a conversation you replied to.

Layering the good seeds with an expansion loop that learns sender domains
from confident hits:

| Strategy | Fetches | % of mailbox | Recall |
| --- | ---: | ---: | ---: |
| Full sweep (the naive baseline) | 58,331 | 100% | 100% |
| ATS + strong phrases + threads | 4,561 | 7.8% | 49.7% |
| Wide seeds + expansion | 11,076 | 19.0% | 93.5% |
| + sent-mail seeds, address-level expansion | 11,778 | 20.2% | **97.1%** |
| + triage of the direct-mail residual | 17,142 | 29.4% | **100%** |

The full experiment series — including the failure analysis of the last 75
missed messages and which negative signals are traps — is in
**[docs/seed-and-expand.md](docs/seed-and-expand.md)**.

## How it works

**Seed & expand**: a LangGraph state machine runs a measured pack of Gmail
queries (your sent threads, ATS senders, LinkedIn's InMail relay, a phrase
pack), classifies what returns, and lets every confident hit teach new
senders to search — looping until the frontier is empty (1–2 hops), then
sweeping the direct-addressed mail nothing matched.

```mermaid
flowchart LR
    plan --> search --> fetch --> classify --> learn
    learn -- "new senders,<br/>domains, threads" --> search
    learn --> sweep["sweep the<br/>direct-mail residual"] --> search
    learn --> report
    classify -. "confident hits teach rules;<br/>negatives silence senders" .-> learn
```

Classification has no hardcoded rules. A first confident extraction teaches a
sender domain `undecided`; a second one *resolving to the same company*
promotes it to a zero-cost carry path — so a recruiting agency that fields
five clients never fuses five hiring processes into one timeline. A negative
verdict silences a sender immediately, address-scoped on shared providers so
one spammer at gmail.com never mutes the rest.

That loop is a testable claim, so it has a test: `evals/run.py --learning`
replays the policy over the gold corpus and asserts a second pass costs fewer
model calls at no accuracy loss.

![The Pipeline page on the demo corpus: how much of the mailbox was swept,
by which mechanism, and what the run taught the sender-rule
table](docs/img/pipeline-demo.png)

(All screenshots in this README are the synthetic demo corpus — invented
messages against real AI 50 employer names, as `jobd demo` discloses. The
tables above are measured on the reference mailbox, which stays private.)

## Quickstart — one API key

```bash
export OPENROUTER_API_KEY=<your key>  # the demo's ~100 flash calls cost cents
docker compose up -d --build
docker compose exec app jobd migrate up
# JOBD_RUN_BUDGET is the hard dollar ceiling for a paid run — classify refuses
# to start without one. $1 is far more than the demo needs.
docker compose exec -e JOBD_RUN_BUDGET=1 app jobd demo
# open http://localhost:8100
```

![The demo: open a company you never told it about, and the hiring process
is already reconstructed — timeline, stages, summary](docs/img/demo.gif)

`jobd demo` seeds a believable synthetic job search — applications, loops,
offers and rejections across all fifty Forbes AI 50 (2026) companies; the
employers are real, every message and event is invented — and classifies it
with the default model (`openrouter/openai/gpt-oss-20b`; override
with `JOBD_MODEL`, or run fully local via `ollama/<model>`). No Gmail
needed. Every dashboard page has content when the ~100 classify calls
finish — a few minutes on a responsive tier; free tiers can throttle to
much longer, so pass a paid flash-class model via `JOBD_MODEL` if you
want it quick. For real
Gmail ingestion and agent-driven setup, see [AGENTS.md](AGENTS.md) and
[docs/gmail-setup.md](docs/gmail-setup.md).

## What you get

* **`jobd scrape`** — the agentic pipeline. Run it once for a full backfill,
  then every day with `--window 3`; it is idempotent by construction, so
  overlap costs one cheap listing pass, never a re-download.
* **A live dashboard** (`jobd serve`, port 8100) — watch a run as it happens:
  the pipeline drawn as a transit rail with the expansion loop animating,
  per-query yields, a funnel of counters, and a feed of discoveries ("New
  company: …", "An offer from …"). Plus the record itself: companies,
  messages, offers, rejections, triage, search.
* **Learned-first classification** — no hardcoded rules. The metadata
  prefilter carries only protocol signals (bulk headers, Gmail labels) and
  learned `sender_rule`s; thread/rule carry resolves repeat senders for
  free; a model (any LiteLLM id, via OpenRouter or local Ollama) reads only
  the undecided residue.
* **Online learning** (`src/jobd/domain/learning.py`) — every model verdict
  teaches: a first confident positive protects the sender's domain, a
  second promotes it to the zero-cost carry path, a negative silences the
  sender immediately (address-scoped on shared providers, so one spammer at
  gmail.com never mutes the rest). Human corrections fan out over the
  backlog and all future mail. `evals/run.py --learning` proves the loop:
  a second pass over the gold corpus must cost fewer model calls at no
  accuracy loss.
* **A chat assistant** (optional, needs a configured model) — a floating
  conversation window over the record: ask about a company, have it draft a
  reply; a human click is always what sends.

## Running against your own Gmail

```bash
# 1. Infrastructure: Postgres (via docker-compose or your own instance):
export DATABASE_URL=postgresql://jobd:jobd@localhost:5432/jobd
export JOBD_LOCAL_STORE=~/.jobd/raw        # local storage (S3 also supported)

pip install -e .          # add ".[llm]" for model-backed extraction
jobd migrate up

# 2. Connect a mailbox (BYO Google OAuth client — docs/gmail-setup.md):
jobd auth gmail --account you@example.com

# 3. Scrape. First run walks the full strategy; --window makes it a daily:
jobd scrape                       # full backfill
jobd scrape --window 3            # what the daily cron runs

# 4. Watch and browse:
cd frontend && npm install && npm run build && cd ..
jobd serve                        # http://127.0.0.1:8100 — /scrape is live
```

Multiple mailboxes: repeat `jobd auth gmail` per account and pass
`--account` per mailbox (repeatable). Every day, from cron or a scheduler:

```
15 6 * * *  jobd scrape --window 3
```

Classification quality scales with the extractor: `JOBD_MODEL` picks any
LiteLLM id (default `openrouter/openai/gpt-oss-20b` with
`OPENROUTER_API_KEY`); cost falls run over run as learned rules absorb
repeat senders. Score any model against the gold corpus with
`python evals/run.py --model <id>`, and the learning loop itself with
`python evals/run.py --learning`.

## Design invariants

The record is **rebuildable**: raw message bytes are stored write-once,
content-addressed, before any row derives from them — `jobd rebuild`
re-derives everything. Ingestion is **idempotent**: re-running any scrape or
import is a no-op for work already done. The prefilter is **metadata-only**:
a message it drops never reaches a model, so on cloud-model deployments most
mail never leaves the machine. Scraping and classification only ever read
your mailbox; the single write path is the reply composer, which drafts and
sends only on an explicit human click (`gmail.compose`, never
`gmail.modify`).

Every model-generated surface — company summaries, chat answers, reply
drafts, audit reasoning — carries the house writing rules from
`src/jobd/domain/style.py`, distilled from the vendored
[avoid-ai-writing](.claude/skills/avoid-ai-writing/SKILL.md) skill (MIT,
Conor Bronsdon), so generated text reads plain and specific rather than
machine-flavored.

## Layout

```
src/jobd/
  scrape/       the seed-and-expand graph (LangGraph), queries, events, facts
  services/     classify, ingest, learning, dashboard, timeline, summaries
  domain/       prefilter, extraction schema, entity resolution — pure logic
  adapters/     gmail, postgres, s3/filesystem storage, LLM providers
  web/          FastAPI JSON API + the built dashboard client
frontend/       Vite/React dashboard (Tailwind + shadcn)
docs/           architecture, schema, experiment series
```
