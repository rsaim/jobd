# AGENTS.md — setting up and operating jobd

Instructions for AI coding agents (Claude Code, Codex, Cursor, or any LLM
with a shell) and equally for humans. Everything here is executable as
written; nothing needs credentials until the section that says so.

## What this is

jobd is a local-first job-search CRM built from your own mailbox. It
ingests mail (Gmail API, or a synthetic demo mailbox), classifies every
message with a free deterministic tier first (sender rules → metadata
prefilter → thread carry → regex extractor), sends only the residue to an
LLM (optional, via OpenRouter/LiteLLM), and compiles the LLM's judgments
back into transparent, human-editable sender rules so tomorrow's run pays
less than today's. A FastAPI server renders the record: companies,
applications, stages, offers, rejections, a review queue, and live run
metrics.

## Fastest path to a working system (no credentials)

Requires Docker with the compose plugin. From the repo root:

```bash
docker compose up -d --build      # postgres + app image
docker compose exec app jobd migrate up
docker compose exec app jobd demo # seed + classify a synthetic mailbox
# open http://localhost:8100
```

`jobd demo` ingests 109 synthetic messages through the real ingest path and
classifies them with the free rule-based extractor. The employers are the
Forbes AI 50 (2026) — all fifty appear, from full interview arcs (an
accepted offer at Anthropic, a declined one at OpenAI, a live onsite loop,
ghostings, agency pitches) down to the acknowledgement-only applies most of
a real pipeline is made of. The companies are real; every message,
recruiter and event is invented, and the two staffing agencies plus the
user's own address live on the reserved `.example` TLD. The dashboard then
has companies, applications, offers, rejections, and a review queue —
every page has content. The demo refuses to run twice against the same
database; `docker compose down -v` resets everything.

Verify it worked:

```bash
docker compose exec db psql -U jobd -c "SELECT count(*) FROM message;"   # 109
docker compose exec db psql -U jobd -c "SELECT canonical_name FROM company;"
curl -s localhost:8100/api/runs | head -c 200
```

## Local development (no Docker)

Requires Python 3.12+, Node 20+, and a reachable Postgres.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[llm,dev]"
(cd frontend && npm ci && npm run build)   # builds into src/jobd/web/static
export DATABASE_URL=postgresql://jobd:jobd@localhost:5432/jobd
.venv/bin/jobd migrate up
.venv/bin/jobd demo
.venv/bin/python -m uvicorn jobd.web.app:app --port 8100
```

`just --list` shows the task runner's recipes (serve, kill, frontend,
daily); the justfile assumes `.venv` exists at the repo root.

## Real mail: Gmail API

Follow `docs/gmail-setup.md` to create a Google Cloud OAuth client
(desktop type) and authorize the account. In short:

```bash
jobd auth gmail            # opens the OAuth consent flow, stores the token
jobd scrape --window 7     # seed-and-expand scrape of the last 7 days
jobd review sweep          # judge model clears what classification punted on
```

Raw mail is stored write-once and content-addressed. Set ONE of:

- `JOBD_LOCAL_STORE=/path/to/raw` — filesystem store (local-first default).
- `JOBD_BUCKET=<s3-bucket>` — S3 store (optional; needs AWS credentials).

## LLM extraction (optional)

Everything runs without a model — the deterministic tier classifies and
whatever it can't settle goes to the review queue for a human. To let a
model clear that queue instead:

```bash
export OPENROUTER_API_KEY=...           # or any LiteLLM-supported provider
jobd classify --all --budget 5          # uses DEFAULT_MODEL (gpt-oss-20b)
jobd review sweep                       # judge model clears the queue
jobd distill --apply                    # compile judgments into sender rules
```

**Picking a model — cost is dominated by input.** Classification feeds a
whole email in and gets a small JSON verdict back: a real 9,256-call run
billed $3.87, which works out to ~6.1k input tokens against ~120 output
tokens. Input price is ~96% of the bill, so compare `$/Mtok in` first and
treat the output rate as a rounding error. Projected onto that profile
(OpenRouter list prices, Sep 2026):

| model | $/Mtok in | $/Mtok out | per 10k calls |
|---|---:|---:|---:|
| `openai/gpt-oss-20b` (default) | 0.030 | 0.130 | **$1.99** |
| `deepseek/deepseek-v4-flash-0731` | 0.065 | 0.180 | $4.18 |
| `google/gemini-2.5-flash-lite` | 0.100 | 0.400 | $6.58 |
| `google/gemini-3.7-flash` | 0.750 | 3.750 | $50.25 |

The Gemini 3.x Flash line is priced for a different job — 25x the default's
input rate. Reach for it as a `--escalation-model` on the residue the cheap
extractor punts on, not as the extractor itself.

Anything you pick must support strict `json_schema` structured output —
`LiteLLMProvider.extract` sends `"strict": True`, and several cheaper ids
(`granite-4.0-h-micro`, `ling-3.0-flash`, `qwen3.7-flash`) do not advertise
it. Set `JOBD_PRICE_PER_MTOK_IN`/`_OUT` to match whatever you choose:
`CreditGuard` enforces `--budget` against those numbers, so a stale pair
mis-sizes the ceiling rather than failing loudly.

Design invariants an agent should preserve when extending this code:

- The free tier always runs first; a model call is the last resort.
- One paid call per thread: the latest message quotes the history, and the
  stored reading in `thread_extraction` answers for every sibling.
- Distilled rules are aggregates-only (no message content is ever re-sent)
  and can only be `negative`/`undecided`, never `positive` — a bad rule may
  widen the review queue, never silently record.
- A `CreditGuard` gates *every* paid call: `before_call` checks the balance
  floor and a run budget, `after_call` records the real billed cost and
  re-checks. Every paid run must declare a budget (`--budget` /
  `JOBD_RUN_BUDGET`) or it refuses to start; batch boundaries stop gracefully
  when credits run low or the budget is spent.
- Everything is idempotent: re-running scrape/ingest/classify over the same
  window is safe by construction (content-addressed storage, upserts).

## Evals

The classifier is measured, not vibes-checked. `evals/` holds a
[DeepEval](https://deepeval.com/) suite that scores the message classifier
against gold labels for the synthetic mailbox (ground truth is known by
construction — `evals/gold_demo.py`): every demo message is a test case,
every quality dimension a deterministic metric.

```bash
pip install -e ".[eval]"
python evals/run.py                                        # free tier, offline
python evals/run.py --model openrouter/openai/gpt-oss-20b   # paid tier
python evals/run.py --verbose                              # per-case report
```

Current scorecard (109 messages; job-relatedness is binary, stage and
company are scored over gold job-related messages). The paid column was
measured on `gemini-3.7-flash`, which was the default when it was run — it
has **not** been re-measured against the current `gpt-oss-20b` default, so
read it as "what the paid tier looked like on that model", not as a claim
about today's default:

| metric | free tier (prefilter + rules) | gemini-3.7-flash (not current default) |
|---|---|---|
| precision | 1.000 | 1.000 |
| recall | 1.000 | 1.000 |
| resolution rate | 0.853 | 1.000 |
| stage accuracy | 0.941 | 0.842 |
| company accuracy | 0.950 | 0.990 |

Read it honestly: the synthetic set is clean by construction, so binary
scores are a floor check, not a hard benchmark — the interesting rows are
resolution (what the free tier punts to a human) and the stage/company
split, where the regex tier actually beats the LLM on stage vocabulary
while losing on company naming. When the demo dataset changes, update
`evals/gold_demo.py` — an assertion fails loudly if the counts drift.

## Where things live

| Path | What |
|---|---|
| `src/jobd/cli/main.py` | every CLI command (`jobd --help`) |
| `src/jobd/services/` | classify, ingest, distill, demo, metrics, dashboard |
| `src/jobd/domain/` | extraction schemas/prompts, prefilter, envelope parsing |
| `src/jobd/adapters/` | gmail, postgres, s3/filesystem storage, LLM providers |
| `src/jobd/scrape/` | the LangGraph seed-and-expand scrape engine |
| `src/jobd/web/` | FastAPI app + built frontend (`static/`) |
| `src/jobd/migrations/` | numbered SQL migrations (`jobd migrate up`) |
| `frontend/` | React/Vite dashboard source |
| `tests/` | pytest suite (`.venv/bin/python -m pytest tests/`) |

## Operating notes for agents

- Migrations: `jobd migrate up` is idempotent; new migrations are paired
  `NNNN_name.up.sql` / `.down.sql` files in `src/jobd/migrations/`.
- The server caches `index.html` — restart it after `npm run build`.
- Long jobs report progress to the `pipeline_run` table; watch with
  `jobd runs --watch` or the dashboard's Runs page.
- The review queue is the contract between the machine and the human:
  resolve items via the Triage page or `jobd review`.
- Never hardcode credentials; every secret arrives via the environment.
