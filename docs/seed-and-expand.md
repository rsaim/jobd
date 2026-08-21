# Seed & expand — finding job mail without sweeping the mailbox

The v2 scraper replaces "download everything, then classify" with a targeted,
self-expanding search. This document records the experiment series the design
came from and the numbers each decision rests on. The implementation lives in
`src/jobd/scrape/`; the queries carry these numbers in their docstrings.

## The problem

v1 ingested the whole mailbox: 58,331 messages fetched for a record that, at
the end, linked 2,631 of them to a company (4.5%). The prefilter kept model
cost sane, but fetch volume — API quota, wall-clock, storage — scaled with
mailbox size, not with how much job mail was in it. At 100K+ messages the
fetch is the bottleneck.

## Ground truth

Every experiment scored retrieval strategies against the already-classified
record: 2,631 company-linked messages ("positives"), 508 companies, 836
applications. Recall = how many of those a strategy's queries would have
found; cost = messages fetched to do it.

## What single query families are worth

| Query family | Fetches | Recall | Precision |
| --- | ---: | ---: | ---: |
| ATS domains (from:/to: greenhouse, lever, ashby…) | 158 | 4.2% | 69.6% |
| `from:inmail-hit-reply@linkedin.com` (+ hit-reply@) | 528 | 14.4% | 71.8% |
| `in:sent` | 923 | 21.0% | 59.8% |
| `in:sent` + full threads of those messages | 2,081 | **51.0%** | 64.5% |
| "interview" anywhere | 2,723 | 28.8% | 27.8% |
| "opportunity" anywhere | 3,700 | 21.6% | 15.4% |
| "talent acquisition"/"talent team" | 206 | 5.7% | 72.8% |
| careers@/talent@/recruiting@ local-parts | 3,542 | 0.3% | 0.2% |

Two lessons: the user's own sent mail plus its threads is the single
strongest seed — half of all job mail sits in a conversation the user replied
to — and the classic "careers@" heuristic is worthless (those addresses send
marketing, not candidacies).

The negative space is just as sharp: Gmail's own
`category:promotions/social/forums` labels covered 29,169 messages and
contained **12** positives (0.04%) — a nearly perfect carve, applied to every
phrase query. The `List-Unsubscribe` header is *not* safe alone: 439 real
positives carry it (ATS mail rides bulk infrastructure).

## Layering to 100%

| Strategy | Fetches | % of mailbox | Recall |
| --- | ---: | ---: | ---: |
| Full sweep (v1) | 58,331 | 100% | 100% |
| ATS + strong phrases + threads | 4,561 | 7.8% | 49.7% |
| + entity expansion loop (domains) | 7,022 | 12.0% | 63.7% |
| Wide seeds + expansion | 11,076 | 19.0% | 93.5% |
| + sent-mail seeds + address-level expansion | 11,778 | 20.2% | **97.1%** |
| + triage of the direct-mail residual | 17,142 | 29.4% | **100%** |

The expansion loop converges in 1–2 hops: each hop takes the positives found
so far, learns their sender domains, addresses, and thread ids, and issues
`from:X OR to:X` / thread-fetch queries for anything new — the same fanout
the `sender_rule` machinery already models, driving retrieval instead of just
classification.

## The hard tail

Everything seeds+expansion miss is personalized recruiter outreach: InMail
through LinkedIn's shared relay, recruiters on personal gmail.com addresses,
and boutique agencies with subjects engineered to dodge job vocabulary
("Alex, that 100x faster engine at Acme?"). Every one of the last 75
missed positives sat in one pool — **direct-addressed to the user, no bulk
header, not category-labeled** (5,364 messages) — which is what the
residual pass reads.

## Gmail API notes

* `resultCountEstimate` is capped and unreliable (three very different
  queries all returned the same number). Measure yield by paging
  `messages.list` (500 ids/call) and counting.
* Search cannot filter by thread id; `threads.get` (minimal format) resolves
  a whole chain in one call.
* Category labels are per-message, so a carved thread stays reachable
  through any sibling a seed catches.

## Caveats

The expansion loop was scored with ground truth standing in for the
classifier, so the measured recall is the retrieval ceiling; the live loop
inherits the real classifier's precision on seeds (mitigated by teaching
rules only from confident positives). Phrase matching used strict substring
search; Gmail's own stemming is looser, so live phrase recall should be equal
or better. All numbers are one mailbox and one job-search pattern — the
framework generalizes, the specific phrase pack may not.
