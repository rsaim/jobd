# jobd

A local-first, agentic job-search CRM built from your own mailbox. jobd finds
the job-related mail in a Gmail account **without downloading the mailbox**,
classifies it into an evidence-linked record — companies, applications,
contacts, stage events — and serves a dashboard to browse and correct it.

The core idea is **seed & expand**: a LangGraph state machine runs a measured
pack of Gmail search queries (your sent threads, ATS senders, LinkedIn's
InMail relay, a phrase pack), classifies what they return, and lets every
confident hit teach new senders to search — looping until the frontier is
empty, then sweeping the direct-addressed mail nothing matched. On the
reference mailbox this reached 97% of known job mail fetching 20% of it, and
100% at 29%. The experiment series behind those numbers:
[docs/seed-and-expand.md](docs/seed-and-expand.md).

```
plan ─→ search ─→ fetch ─→ classify ─→ learn ─┬─→ search   (new entities)
                                              ├─→ sweep ─→ search
                                              └─→ report
```

## Quickstart — no credentials needed

```bash
docker compose up -d --build
docker compose exec app jobd migrate up
docker compose exec app jobd demo     # synthetic mailbox through the real pipeline
# open http://localhost:8100
```

`jobd demo` seeds a believable synthetic job search (fictional companies on
the reserved `.example` TLD) and classifies it with the free deterministic
tier — no API keys, no Gmail, no cloud. Every dashboard page has content in
under two minutes. For real Gmail ingestion, LLM extraction, and agent-driven
setup, see [AGENTS.md](AGENTS.md) and [docs/gmail-setup.md](docs/gmail-setup.md).

## What you get

* **`jobd scrape`** — the agentic pipeline. Run it once for a full backfill,
  then every day with `--window 3`; it is idempotent by construction, so
  overlap costs one cheap listing pass, never a re-download.
* **A live dashboard** (`jobd serve`, port 8100) — watch a run as it happens:
  the pipeline drawn as a transit rail with the expansion loop animating,
  per-query yields, a funnel of counters, and a feed of discoveries ("New
  company: …", "An offer from …"). Plus the record itself: companies,
  messages, offers, rejections, triage, search.
* **Deterministic-first classification** — a metadata prefilter and a
  rule-based extractor resolve most mail for free; a model (any LiteLLM id,
  via OpenRouter or local) reads only what they can't. `rulebased` runs the
  whole pipeline offline.
* **Online learning** — every confident extraction teaches a `sender_rule`;
  human corrections fan out over the backlog and all future mail.
* **A chat assistant** (optional, needs a configured model) — a floating
  conversation window over the record: ask about a company, have it draft a
  reply; a human click is always what sends.

## Quick start

```bash
# 1. Infrastructure: Postgres (see infra/), and either an S3 bucket for raw
#    mail or a local directory:
export DATABASE_URL=postgresql://jobd:jobd@localhost:5432/jobd
export JOBD_LOCAL_STORE=~/.jobd/raw        # or: export JOBD_BUCKET=<bucket>

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

Classification quality scales with the extractor: set `JOBD_MODEL` (e.g.
`openrouter/google/gemini-2.5-flash-lite` with `OPENROUTER_API_KEY`) and the
undecided residue goes through a real model instead of the review queue.

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
