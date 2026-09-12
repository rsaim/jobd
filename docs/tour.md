# A tour of jobd

What the README compresses, at full length: the pieces you get, how to run
it against your own Gmail, and the invariants the design holds. The
retrieval experiment series lives in [seed-and-expand.md](seed-and-expand.md);
the module map is in [AGENTS.md](../AGENTS.md).

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
* **A reply composer on every company page** — pre-addressed from the
  thread, a tone picker fed by your saved prompts, a model-drafted body you
  edit, and a send that only ever happens on your click (`gmail.compose`,
  never `gmail.modify`).
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
jobd auth gmail    # the account is read back from the OAuth token

# 3. Scrape. First run walks the full strategy; --window makes it a daily:
jobd scrape                       # full backfill
jobd scrape --window 3            # what the daily cron runs

# 4. Watch and browse:
cd frontend && npm install && npm run build && cd ..
jobd serve                        # http://127.0.0.1:8100 — /scrape is live
```

Multiple mailboxes: run `jobd auth gmail` once per account. Every day, from
cron or a scheduler:

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
[avoid-ai-writing](../.claude/skills/avoid-ai-writing/SKILL.md) skill (MIT,
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
