# The numbers

Every headline figure jobd shows or claims, in one place: what is measured,
what is estimated, and how to re-verify each one. The README links here
instead of carrying the tables itself.

Two different kinds of number live here. **Retrieval numbers** are
measurements — scored against a fully labeled reference mailbox.
**Dashboard figures** are derived from your own mail; a few of them lean on
declared assumptions (a mailbox records that an onsite was scheduled, never
that you spent eight hours preparing for it), and every assumption is a
constant you can edit.

## Retrieval: 97% of the job mail for 20% of the fetches

Downloading the mailbox to classify it means fetching 58,331 messages to
find the 2,631 that matter — 4.5% signal. So the mailbox got labeled and
retrieval strategies got scored against it. The first surprise: the obvious
heuristic — searching `careers@` / `talent@` — has **0.2% precision**
(3,542 fetches, 7 real). What works is your own sent mail: half of all job
mail sits in a conversation you replied to.

| Strategy | Fetches | % of mailbox | Recall |
| --- | ---: | ---: | ---: |
| Full sweep (the naive baseline) | 58,331 | 100% | 100% |
| ATS + strong phrases + threads | 4,561 | 7.8% | 49.7% |
| Wide seeds + expansion | 11,076 | 19.0% | 93.5% |
| + sent-mail seeds, address-level expansion | 11,778 | 20.2% | **97.1%** |
| + triage of the direct-mail residual | 17,142 | 29.4% | **100%** |

The full experiment series — per-query ablations, the failure analysis of
the last 75 missed messages, which negative signals are traps — is in
**[seed-and-expand.md](seed-and-expand.md)**. These are measured on the
reference mailbox, which stays private; `evals/run.py` re-verifies the
claims against the labeled set.

## Dashboard figures: derived vs. assumed

Every count on the dashboard is derived from the mail, never typed in. The
hour figures are **estimates anchored on corroborated rounds, not
measurements**. Each dashboard tile carries the same disclosure in its hint
text.

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
in [`services/dashboard.py`](../src/jobd/services/dashboard.py) — one
place; change them if yours differ and every figure follows.

## The learning claim

"Cheaper each run" is a testable claim, so it has a test:
`evals/run.py --learning` replays the corpus twice and asserts the second
pass costs fewer model calls at no accuracy loss — every confident verdict
distills into a standing sender rule, so tomorrow's pass never asks a model
about mail today's already settled.
