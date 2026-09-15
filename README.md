<h1 align="center">jobd</h1>

<p align="center"><b>Your job search, reconstructed from your own mail.</b></p>

<p align="center">
A local-first job-search CRM that builds itself from your Gmail — companies,
applications, stage timelines, offers — every row linked to the message that
evidences it. No mailbox download, no hosted service, no hardcoded sender
lists.
</p>

<p align="center">
  <a href="https://132-145-188-13.sslip.io"><img src="https://img.shields.io/badge/live%20demo-demo%20%2F%20demo-1a56db" alt="Live demo, log in as demo/demo"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3da639" alt="MIT license"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776ab" alt="Python 3.12+"></a>
  <a href="https://codespaces.new/rsaim/jobd"><img src="https://img.shields.io/badge/demo-no%20credentials-181717?logo=github" alt="Demo runs with no credentials"></a>
</p>

<p align="center">
  <a href="#quickstart--no-credentials">Quickstart</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#the-problem-measured">The numbers</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#what-leaves-your-machine">Privacy</a> ·
  <a href="docs/tour.md">Docs</a>
</p>

---

**Finds 97% of the job-related mail in a Gmail account by fetching 20% of
it. 100% at 29%.** A search loop that learns which senders to ask about
next, and gets cheaper every run. Spreadsheet trackers ask you to type the
record in by hand; inbox-sync tools download the mailbox wholesale. jobd
does neither.

![The Today dashboard on the demo corpus: a company funnel, an interview-day
heatmap, estimated prep and in-room hours, and median weeks to an offer —
every figure derived from the mail itself](docs/img/today-demo.png)

## What you get

* **One command a day.** `jobd scrape` backfills once, then runs as a
  three-day-window cron. Idempotent by construction: overlap costs one cheap
  listing pass, never a re-download.
* **A live dashboard** — watch a run as it happens (the pipeline drawn as a
  transit rail, per-query yields, a feed of discoveries), then browse the
  record it built: companies, applications, offers, rejections, a review
  queue, search.
* **Learned-first classification** — protocol signals and learned sender
  rules decide most mail for free; thread carry resolves repeat senders; a
  model reads only the undecided residue.
* **A loop that provably gets cheaper** — every confident verdict distills
  into a standing rule, and `evals/run.py --learning` asserts a second pass
  costs fewer model calls at no accuracy loss.
* **A reply composer on every company page** — pre-addressed from the
  thread, a tone picker fed by your saved prompts, a drafted body you edit.
  Sending only ever happens on your click.
* **Any model** — any LiteLLM id via OpenRouter, or local Ollama, in which
  case no mail leaves your machine at all. An optional chat dock answers
  questions over the record.

## Quickstart — no credentials

No local setup at all:
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rsaim/jobd)
— the stack builds itself, the demo loads, the dashboard opens on port 8100.

Or locally:

```bash
docker compose up -d --build
docker compose exec app jobd migrate up
docker compose exec app jobd demo
# open http://localhost:8100
```

The demo needs no key: its corpus is synthetic, so classification replays
the gold labels through the real pipeline — prefilter, thread carry, the
learning loop teaching rules — deterministically and offline. A model (and
`OPENROUTER_API_KEY` + `JOBD_RUN_BUDGET`) enters the picture for live
classify runs against real mail, the chat dock, and summaries.

![The demo: open a company you never told it about, and the hiring process
is already reconstructed — timeline, stages, summary](docs/img/demo.gif)

The demo is a synthetic job search across the Forbes AI 50 (2026) — real
employers, invented mail — through the real pipeline. For your own Gmail,
model choices, and the daily cron: **[docs/tour.md](docs/tour.md)** and
[AGENTS.md](AGENTS.md).

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
  <img alt="The seed-and-expand loop: search, fetch, classify, learn, and a sweep of the
direct-mail residual run clockwise, while classify and learn write distilled
sender rules into a central hub — so every pass costs less than the one
before" src="docs/img/seed-expand-loop.png">
</picture>

*`plan` seeds the first search; `report` closes the run once the frontier is
empty. The dashed spokes are the part that compounds: model verdicts distill
into standing rules, so the next pass asks the model less.*

No hardcoded rules anywhere. A first confident extraction teaches a sender
domain `undecided`; a second one *resolving to the same company* promotes
it to a zero-cost carry path — so an agency fielding five clients never
fuses five hiring processes into one timeline. Negatives silence a sender
immediately. The loop is a testable claim, so it has a test:
`evals/run.py --learning` asserts a second pass costs fewer model calls at
no accuracy loss.

## What leaves your machine

jobd ships no service and phones home to nothing — there is nowhere to
phone. What leaves depends only on the model you pick:

| Configuration | Leaves your machine |
|---|---|
| Local model (`ollama/<id>`) | **Nothing** beyond the mail providers you already use. |
| Cloud model | Only messages that survive the metadata prefilter, inside the extraction prompt — 41% of the reference mailbox never reached a model. |
| `jobd demo` | Nothing real exists in this mode. |

The Gmail scope is `gmail.readonly` + `gmail.compose` — it can read mail and
send *your* composed reply, never label, archive, or delete
(`gmail.modify` is never requested). The OAuth client is yours
([docs/gmail-setup.md](docs/gmail-setup.md), ~10 minutes): jobd ships no
shared client id, so this project is never a data processor for your mail.
Tokens live in the OS keyring; raw mail is stored write-once and
content-addressed on your disk or an S3 bucket you own. The full accounting
— every credential, every write path — is in **[SECURITY.md](SECURITY.md)**;
any discrepancy between that file and the code is a bug.

## Reply without leaving the record

Every company page ends in a composer: pre-addressed from the thread, a
tone picker fed by your saved prompts, a drafted body you edit. Drafts are
model-written under house style rules that keep them plain; **sending only
ever happens on your click**.

![A company page on the demo corpus: the reconstructed summary and
timeline, and below them the reply composer with a generated
draft](docs/img/reply-demo.png)

(Screenshots and the GIF are the synthetic demo corpus — invented messages
against real AI 50 employer names, as `jobd demo` discloses. The retrieval
tables are measured on the reference mailbox, which stays private.)

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
