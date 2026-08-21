# Connecting Gmail (M4 gate 1)

You create the OAuth client. jobd-ai ships none, and never will.

A shared client id would make this project a data processor for everyone who
install it — the hosted-service posture PRD §4 permanently rule out. Cost is
this page of setup. PRD §10 accept that cost explicitly.

Roughly 10 minutes, once per Google account.

---

## 1. Google Cloud project

1. https://console.cloud.google.com/projectcreate
2. Name it anything. `jobd-ai` is fine.
3. Select it.

## 2. Enable the Gmail API

APIs & Services → Library → search "Gmail API" → **Enable**.

Nothing works before this, and the error you get without it does not say so.

## 3. OAuth consent screen

APIs & Services → OAuth consent screen.

| Field | Value |
|---|---|
| User type | **External** |
| App name | anything |
| User support email | your address |
| Developer contact | your address |
| Scopes | leave empty here — the app requests `gmail.readonly` at consent time |
| Test users | **add your own Gmail address** |

Test users is not optional and not a formality. A Testing-mode app admits only
the accounts on that list, so skipping the row get you `Error 403:
access_denied` at consent time — "can only be accessed by developer-approved
testers" — even though the project, the client, and the secret are all correct.
Add the exact mailbox you intend to ingest, not an alias that forwards to it.

Newer Console builds move this to **APIs & Services → OAuth consent screen →
Audience**. Same list.

**Publishing status: leave it in Testing.** Do not submit for verification.

Consequence: refresh tokens expire after **7 days**. For one operator that is
one `jobd auth gmail` per week. Going to Production mean Google CASA
verification, which PRD §4 permanently rule out — jobd is not becoming a
verified app that handle other people's mail.

## 4. Create the client

APIs & Services → Credentials → Create credentials → **OAuth client ID**.

Application type: **Desktop app**. Not Web application — a web client cannot use
the loopback redirect this flow need, and `jobd auth gmail` will tell you so if
you pick wrong.

Download the JSON. Put it on the persistent volume, **not** in the repo:

```bash
mkdir -p ~/.jobd
mv ~/Downloads/client_secret_*.json ~/.jobd/gmail_client_secret.json
chmod 600 ~/.jobd/gmail_client_secret.json
```

`.gitignore` already block `client_secret*.json`, but the repo is not where it
belong regardless.

## 5. Consent

```bash
jobd auth gmail
```

Prints a URL. Open it on your laptop, approve, done. The command reads your
address back from the token, so it cannot be stored under a typo.

**Google will warn "Google hasn't verified this app."** That is correct and
expected — it is *your* unverified app, holding *your* credentials, reading
*your* mail. Advanced → Go to jobd-ai (unsafe).

### In a container

The flow listen on `127.0.0.1:8765` **inside** the container while your browser
run on the host. Same shape as the `aws login --remote` problem from M1, with a
different fix: Codespaces and VS Code Remote forward the port automatically once
it start listening. Watch the Ports panel.

Different port if 8765 is taken:

```bash
jobd auth gmail --port 9999
```

### No forwarding at all

Plain SSH, a bare `docker run`, a remote box — nowhere for the callback to
land. Skip the server entirely:

```bash
jobd auth gmail --manual
```

Prints the consent URL. Approve it in any browser on any machine. The browser
then fail to load a `localhost:8765` page — expected, nothing is listening —
and the code is sitting in the address bar. Copy the whole URL, paste it back.

The redirect still point at loopback because a Desktop client may register
nothing else. Two alternatives that look right and are not: Google disabled the
OOB redirect (`urn:ietf:wg:oauth:2.0:oob`) in October 2022 and deleted
`run_console()` with it, and the device-code flow's scope allowlist does not
include Gmail.

PKCE still apply — the verifier never leave the process — so a pasted code is
useless to anyone who see it without this run's memory. What the paste *does*
carry is `state`, checked against the URL this run printed, so a stale URL from
an earlier attempt is refused rather than exchanged.

### Verify

```bash
jobd auth status
```

Shows the token backend and connected accounts. If it say *no OS keyring*, the
token is a 0600 file on the volume rather than in a keychain — see
SECURITY.md §3. Outside the repo, outside Postgres, weaker than a keychain, and
it says so rather than pretending.

## 6. Ingest

```bash
export JOBD_BUCKET=$(cd infra/terraform/storage && terraform output -raw bucket_name)
jobd migrate up
jobd ingest gmail --account you@gmail.com
```

Filesystem archive instead of S3 (no bucket needed):

```bash
jobd ingest gmail --account you@gmail.com --local-store ~/jobd-raw
```

Start small on a five-year mailbox:

```bash
jobd ingest gmail --account you@gmail.com --since 2026-07-01
```

### Prove gate 2 yourself

Run it twice. Second run must print zeros:

```
  fetched          1483
  stored (new)     0
  already stored   1483
  rows inserted    0
  rows existing    1483
```

Non-zero `stored` or `rows inserted` on an unchanged mailbox is a bug, not a
quirk.

### Prove gate 3 yourself

Third run resume from the stored cursor. `api calls` collapse from
*2 + one per message* to *2 + one per new message*.

---

## When it breaks

| Symptom | Cause | Fix |
|---|---|---|
| `Error 403: access_denied`, "developer-approved testers" | Your address is not a test user | Consent screen → Audience → Test users → add it. Effective immediately. Do **not** "Publish app" to escape this — unverified-and-published is worse, and restricted scopes stay blocked |
| `does not look like an OAuth client secret` | Wrong JSON downloaded | Get the **Desktop app** client from Credentials |
| `That is a *Web application* client` | Wrong application type | Create a Desktop app client |
| Browser hangs on the callback | Port not forwarded | Check the Ports panel, `--port` something forwarded, or `--manual` and skip the callback |
| `Google rejected that code` | Codes are single-use and expire in minutes | Re-run `jobd auth gmail --manual`, paste the **new** URL |
| `That URL is from a different consent attempt` | Pasted an older URL from the scrollback | Use the URL this run printed |
| `Refresh failed … grant was probably revoked` | 7-day test-mode expiry, or you revoked it | `jobd auth gmail` again |
| `Gmail no longer has history from …` | Away longer than Gmail keeps history (~1 week) | `jobd ingest gmail --account … --full`. Duplicates nothing |
| `No raw storage configured` | Neither `--bucket`/`JOBD_BUCKET` nor `--local-store` | Set one. Raw must land durably before anything derive from it (I3) |

## What leaves your machine

Your mail goes to your S3 bucket and nowhere else. Gmail already have it. No
jobd-ai server exist. See SECURITY.md §1.

M4 does not call an LLM at all — it moves bytes. The cloud-LLM disclosure begin
in M5, and only for pre-filtered candidates.
