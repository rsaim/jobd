# Security

jobd processes a job seeker's entire mail history, so this document states
exactly what leaves your machine under every configuration, which
credentials exist, and what each one can do. Any discrepancy between this
file and the code is a bug — please open an issue.

## 1. What leaves your machine

| Configuration | Leaves your machine | Never leaves |
|---|---|---|
| Live runs | Only messages that survive the metadata prefilter, inside the extraction prompt — one call per thread. | The raw mailbox as a whole; everything the prefilter rejected (41% of the reference mailbox never reached a model); your credentials. |
| `jobd demo` | Synthetic messages only. | Any real personal data — none exists in this mode. |
| Chat dock / summaries (`JOBD_CHAT_MODEL` set) | Any message you ask about — not only prefilter survivors. The panel header names the model reading your mail. | When unset, the routes answer 404: the feature does not exist, there is no toggle to trip. |

Learned `sender_rule`s are **aggregates only** — a rule stores a domain or
address and a verdict, never message content, and distillation re-sends
nothing.

## 2. Write paths

Scraping and classification only ever read your mailbox. The single write
path is the reply composer: it drafts, and sends only on an explicit human
click. No agentic path holds a send-capable tool.

## 3. Storage

Raw message bytes are stored write-once and content-addressed, in a local
directory (`JOBD_LOCAL_STORE`).
The Postgres record is derived and rebuildable from raw (`jobd rebuild`).
Nothing is ever sent to any service operated by this project — there is no
such service.

## 4. Credentials and scopes

| Credential | Where it lives | What it can do |
|---|---|---|
| Google OAuth token | OS keyring (via `keyring`) | `gmail.readonly` + `gmail.compose` — read mail, create drafts, send *your* composed reply. Never `gmail.modify`: it cannot label, archive, or delete. |
| OAuth client secret | You create it; jobd ships none | Nothing by itself; pairing it with consent is the whole ceremony (`docs/gmail-setup.md`). |
| `OPENROUTER_API_KEY` (or any LiteLLM provider key) | Environment / `.env` (gitignored) | Paid model calls, gated by `CreditGuard`: every run needs a declared budget (`--budget` / `JOBD_RUN_BUDGET`) or it refuses to start. |
| `JOBD_LINKEDIN_TOKEN` (optional) | `~/.jobd_linkedin_token` | Authenticates the local push endpoint; absent, the endpoint answers 503. |
| Postgres credentials | `DATABASE_URL` / compose defaults | The local record only. |

## 5. Reporting

Open a GitHub issue. If the finding is sensitive, say so in the issue
without details and a private channel will be arranged.
