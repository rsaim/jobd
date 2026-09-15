<h1 align="center">jobd</h1>

<p align="center"><b>A job-search daemon. Your search, reconstructed from your own mail.</b></p>

<p align="center">
An agentic pipeline — a LangGraph search loop over Gmail, an LLM reading only
what cheap deterministic tiers could not decide — that compiles a local-first
job-search CRM: companies, applications, stage timelines, offers, every row
linked to the message that evidences it. No mailbox download, no hosted
service, no hardcoded sender lists.
</p>

<p align="center">
  <a href="https://jobd.demo.rsaim.dev"><img src="https://img.shields.io/badge/live_demo-demo_/_demo-1a56db?logo=icloud&logoColor=white" alt="Live demo, log in as demo/demo"></a>
  <a href="https://codespaces.new/rsaim/jobd"><img src="https://img.shields.io/badge/Codespaces-no_credentials-181717?logo=github&logoColor=white" alt="Run it in Codespaces with no credentials"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-3da639?logo=opensourceinitiative&logoColor=white" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#what-a-job-search-actually-cost-you">What it cost you</a> ·
  <a href="#try-it--no-credentials">Try it</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#the-problem-measured">The numbers</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#what-leaves-your-machine">Privacy</a> ·
  <a href="docs/tour.md">Docs</a>
</p>

---

**Finds 97% of the job-related mail in a Gmail account by fetching 20% of
it. 100% at 29%.** A self-improving agent: every confident verdict distills
into a standing rule, so tomorrow's pass asks an LLM about mail today's
already settled — cheaper each run, and more accurate, because a learned
sender is one the cheap tiers can no longer wrongly drop. Spreadsheet
trackers ask you to type the record in by hand; inbox-sync tools download
the mailbox wholesale. jobd does neither.

![The Today dashboard on the demo corpus: a company funnel, an interview-day
heatmap, estimated prep and in-room hours, and median weeks to an offer —
every figure derived from the mail itself](docs/img/today-demo.png)

## What a job search actually cost you

Nobody records this, so nobody can tell you. The record can: a funnel of
429 companies down to 8 offers, 150 confirmed interview rounds, 11.2 weeks
from first contact to an offer, a 26% ghost rate — and the two numbers no
spreadsheet has ever held.

**~126h in rooms. ~437h prepping** — the invisible half of a job search:
the practice before a technical round, the system-design sweep before an
onsite, the standing weekly grind in any month the search was live. 437
hours that never appeared on anyone's calendar.

Every count is derived from the mail, never typed in. The hour figures are
**estimates anchored on corroborated rounds, not measurements** — a mailbox
records that an onsite was scheduled, never that you spent eight hours
preparing for it. The weights live in one place
([`services/dashboard.py`](src/jobd/services/dashboard.py)); change them if
yours differ and every figure follows.

<details>
<summary><b>How each figure is computed</b> (every one is auditable)</summary>

| Metric | How it is computed | Derived or assumed |
| --- | --- | --- |
| **Confirmed rounds** | First `stage_event` per application per stage. A recruiter screen only counts if your *own* sent mail lands within −7/+14 days of it — a scheduled call that never happened doesn't count. | Derived |
| **In rooms** | Confirmed rounds × a per-stage length: 0.5h screen, 1h technical, 3h onsite (a same-day loop, not one meeting). | Assumed weights |
| **Prepping** | Confirmed rounds × a per-stage prep weight: 0.5h screen, 2h phone, 4h technical, 8h onsite — plus 8h/month for every month holding at least one round (the standing practice no single round can claim). | Assumed weights |
| **Weeks to an offer** | Median across offers of: your first message to that company → the offer date. | Derived |
| **Ghost rate** | Of companies you actually engaged, the share whose thread went quiet: last message **outbound**, 21+ days old, not in a terminal stage. Their silence, not yours. | Derived |
| **Funnel** | Companies → engaged (you wrote) → replied → interviewed (≥1 confirmed round) → offers. Counts distinct companies; rounds are counted separately. | Derived |
| **Response rate** | Applications that got a reply ÷ applications you reached out to. | Derived |

The weights are `_INTERVIEW_HOURS`, `_PREP_HOURS`, `_PREP_BASELINE_MONTHLY`
in [`services/dashboard.py`](src/jobd/services/dashboard.py). Each dashboard
tile carries the same disclosure in its hint text.

</details>

## Try it — no credentials

The demo is **hosted**: [jobd.demo.rsaim.dev](https://jobd.demo.rsaim.dev),
log in as `demo` / `demo`. Nothing to install, nothing to sign up for.

Or run it yourself —
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rsaim/jobd)
— the stack builds itself, the demo loads, the dashboard opens on port 8100.
Locally:

```bash
docker compose up -d --build
docker compose exec app jobd migrate up
docker compose exec app jobd demo
# open http://localhost:8100
```

The demo needs no key: its corpus is synthetic — a job search across the
Forbes AI 50 (2026), invented mail against real employer names — replayed
through the real pipeline, deterministically and offline. A model (and
`OPENROUTER_API_KEY`) enters the picture only for live runs against real
mail. For your own Gmail, model choices, and the daily cron:
**[docs/tour.md](docs/tour.md)**.

![The demo: open a company you never told it about, and the hiring process
is already reconstructed — timeline, stages, summary](docs/img/demo.gif)

## What you get

* **One command a day.** `jobd scrape` backfills once, then runs as a
  three-day-window cron. Idempotent by construction: overlap costs one
  cheap listing pass, never a re-download.
* **A live dashboard** — watch a run as it happens (the pipeline drawn as a
  transit rail, per-query yields, a feed of discoveries), then browse the
  record it built: companies, offers, rejections, a review queue, search.
* **Learned-first classification** — protocol signals and learned sender
  rules decide most mail for free; a model reads only the undecided residue.
* **A reply composer on every company page** — pre-addressed from the
  thread, a tone picker fed by your saved prompts, a drafted body you edit.
  **Sending only ever happens on your click.**
* **Any model** — any LiteLLM id via OpenRouter, or local Ollama, in which
  case no mail leaves your machine at all.

![A company page on the demo corpus: the reconstructed summary and
timeline, and below them the reply composer with a generated
draft](docs/img/reply-demo.png)

## The problem, measured

Downloading the mailbox to classify it means fetching 58,331 messages to
find the 2,631 that matter — 4.5% signal. So the mailbox got labeled and
retrieval strategies got scored against it. The first surprise: the
obvious heuristic — searching `careers@` / `talent@` — has **0.2%
precision** (3,542 fetches, 7 real). What works is your own sent mail:
half of all job mail sits in a conversation you replied to.

| Strategy | Fetches | % of mailbox | Recall |
| --- | ---: | ---: | ---: |
| Full sweep (the naive baseline) | 58,331 | 100% | 100% |
| ATS + strong phrases + threads | 4,561 | 7.8% | 49.7% |
| Wide seeds + expansion | 11,076 | 19.0% | 93.5% |
| + sent-mail seeds, address-level expansion | 11,778 | 20.2% | **97.1%** |
| + triage of the direct-mail residual | 17,142 | 29.4% | **100%** |

The full experiment series — per-query ablations, the failure analysis of
the last 75 missed messages, which negative signals are traps — is in
**[docs/seed-and-expand.md](docs/seed-and-expand.md)**.

## How it works

A LangGraph state machine runs a measured pack of Gmail queries, classifies
what returns, and lets every confident hit teach new senders to search —
looping until the frontier is empty (1–2 hops), then sweeping the
direct-addressed mail nothing matched.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/seed-expand-loop-dark.png">
  <img alt="The seed-and-expand loop: mail enters from the Gmail API and the record
lands in Postgres, while search, fetch, classify, learn and a sweep of the
direct-mail residual run clockwise. Classify and learn distil model verdicts
into one shared store of learned sender rules, and a bar chart shows model
calls falling with every pass" src="docs/img/seed-expand-loop.png">
</picture>

*`plan` seeds the first search; `report` closes the run once the frontier is
empty. The dashed spokes are the part that compounds: model verdicts distill
into standing rules, so the next pass asks the model less.*

Here is the loop live — a one-day sync against a real mailbox. 248 queries
in 24 seconds, and every message they surface is already in the record, so
nothing is re-fetched and no model is paid: idempotency on camera.

![A one-day sync, recorded live: the station rail lights up plan through
report, 248 Gmail queries run, all 77 matched ids are recognized as already
ingested, and the run closes having spent $0.00](docs/img/sync-demo.gif)

No hardcoded rules anywhere. A first confident extraction teaches a sender
domain `undecided`; a second one *resolving to the same company* promotes
it to a zero-cost carry path — so an agency fielding five clients never
fuses five hiring processes into one timeline. Negatives silence a sender
immediately. The loop is a testable claim, so it has a test:
`evals/run.py --learning` asserts a second pass costs fewer model calls at
no accuracy loss.

![The learned-rules page on the demo corpus: sender rules taught by the
pipeline, each with the verdict it carries and the extraction that taught
it](docs/img/rules-demo.png)

## What leaves your machine

jobd ships no service and phones home to nothing — there is nowhere to
phone. What leaves depends only on the model you pick:

| Configuration | Leaves your machine |
|---|---|
| Local model (`ollama/<id>`) | **Nothing** beyond the mail providers you already use. |
| Cloud model | Only messages that survive the metadata prefilter, inside the extraction prompt — 41% of the reference mailbox never reached a model. |
| `jobd demo` | Nothing real exists in this mode. |

![The pipeline page on the demo corpus: how many messages the free tiers
absorbed before any model was asked, and the rules the run
taught](docs/img/pipeline-demo.png)

The Gmail scope is `gmail.readonly` + `gmail.compose` — it can read mail and
send *your* composed reply, never label, archive, or delete
(`gmail.modify` is never requested). The OAuth client is yours
([docs/gmail-setup.md](docs/gmail-setup.md), ~10 minutes): jobd ships no
shared client id, so this project is never a data processor for your mail.
Tokens live in the OS keyring; raw mail is stored write-once and
content-addressed on your disk or an S3 bucket you own. The full accounting
— every credential, every write path — is in **[SECURITY.md](SECURITY.md)**;
any discrepancy between that file and the code is a bug.

(Screenshots and GIFs are the synthetic demo corpus — invented messages
against real AI 50 employer names, as `jobd demo` discloses — except the
sync recording above, a real-run capture that shows only aggregate
counters. The retrieval tables are measured on the reference mailbox,
which stays private.)

## More

* **[docs/tour.md](docs/tour.md)** — every feature, Gmail setup, design
  invariants, layout
* **[docs/seed-and-expand.md](docs/seed-and-expand.md)** — the experiment
  series behind the numbers
* **[docs/architecture.md](docs/architecture.md)** — how the pieces fit
* **[docs/gmail-setup.md](docs/gmail-setup.md)** — create your OAuth client,
  once, in about ten minutes
* **[AGENTS.md](AGENTS.md)** — operating manual, for humans and agents

## Contributing

Issues and pull requests are welcome. [AGENTS.md](AGENTS.md) is the
operating manual — executable as written, for humans and coding agents
alike — and `jobd demo` gives every contributor a fully populated system
with no credentials. `pytest` runs the suite; `evals/run.py` re-verifies
the retrieval and learning claims. Security findings:
[SECURITY.md](SECURITY.md#5-reporting).

## License

[MIT](LICENSE) © Saim Raza
