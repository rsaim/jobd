# Security

jobd-ai process a job seeker entire mail history. This document state exactly
what leave your machine under every configuration, which credentials exist, and
what they can do. Discrepancy between this file and code = bug. Open issue.

Requirements here come from [`docs/jobd-prd.md`](docs/jobd-prd.md) §7 and
invariants in [`docs/build-guide.md`](docs/build-guide.md) §1.

---

## 1. What leaves your machine

| Configuration | Leaves your machine | Never leaves |
|---|---|---|
| `local` + local LLM (Ollama) | **Nothing.** No outbound calls beyond mail providers you already use. | Everything: raw mail, extractions, embeddings, drafts. |
| `local` + cloud LLM | Pre-filtered candidate messages only — ones surviving deterministic ATS/vocabulary filter — plus extraction prompt. | Raw mailbox as whole; anything pre-filter rejected; S3 bucket contents; your credentials. |
| `demo` | Synthetic data only. | Any real personal data. None exist in this profile. |
| Chat panel (`JOBD_CHAT_MODEL` set) | *Any* message you ask about — not only pre-filtered candidates. Panel header names model reading mail. | Same as above when unset: feature does not exist, no toggle, no route. |

Two consequences, said plain:

- **Your raw mail never uploaded anywhere except your own S3 bucket.** No
  jobd-ai server. No hosted multi-tenant service, and per PRD §4 there never
  will be — permanent stance, not roadmap item.
- **Cloud-LLM path is genuine disclosure.** Messages passing pre-filter go to
  whichever provider you configure. Unacceptable for your data? Run Ollama.
  First-class supported configuration, not degraded one.

## 2. Credentials

jobd-ai use **two AWS credentials, different lifetimes, different blast radii**.
Conflate them = scoping claims below unverifiable.

### Bootstrap credential — for `terraform apply` only

AWS Management Console session from `aws login` (add `--remote` in Codespace or
over SSH — see [`infra/terraform/README.md`](infra/terraform/README.md)).
Short-lived, auto-refreshing, never written to repo. Broad by necessity: it make
bucket and runtime IAM user.

Session belong to `jobd-admin` IAM user made by
[`infra/terraform/bootstrap`](infra/terraform/bootstrap) — **not account root
user**. Root cannot be meaningfully scoped or revoked, and bypass guardrails. So
root sign in exactly once, make admin user, then never again. `jobd-admin` hold
console password and MFA but deliberately no access keys: interactive identity,
so long-lived key = pure downside.

No IAM Identity Center instance on this account (`aws sso-admin list-instances`
return none), no AWS Organization. So `aws sso login` unavailable. `aws login`
get console session instead. Enable Identity Center = upgrade path if this grow
past one operator. Storage module unchanged.

### Runtime credential — what jobd actually run on

Made by Terraform, not by hand. Dedicated IAM user (`/jobd/jobd-runtime`). Only
policy grant:

- `s3:ListBucket`, `s3:GetBucketLocation`, `s3:ListBucketMultipartUploads` on
  **one bucket ARN**
- `s3:GetObject`, `s3:PutObject`, `s3:AbortMultipartUpload`,
  `s3:ListMultipartUploadParts` on **that bucket objects**

No wildcard resources. No other service. Notably **no `s3:DeleteObject`**: raw
messages immutable once written. Daemon have no reason to delete, and
compromised daemon cannot destroy your archive. Retention = lifecycle policy
job. Deletion need bootstrap credential.

### Where credentials live

On persistent volume at `~/.jobd/aws/`, never in workspace. `~/.aws` is symlink
to it, so AWS CLI default paths resolve onto volume with no `AWS_*_FILE`
overrides. In devcontainer, only declared mounts survive rebuild. Real `~/.aws`
would mean re-authenticating every time.

Repo root is workspace, so `.gitignore` block `.aws/`, `credentials`, `*.tfvars`
and all Terraform state as second line of defence.

Mail credentials follow same rule: per-user OAuth apps (no shared client ID),
tokens in OS-side secret storage, never in Postgres, never in git.

**No shared OAuth client exist in this tree, and never will.** One would make
jobd-ai a data processor for everyone who install it — the hosted-service
posture §4 permanently rule out. You create your own client; see
[`docs/gmail-setup.md`](docs/gmail-setup.md).

Scope requested is `gmail.readonly` and nothing else. M8 need `gmail.compose`
for drafts and will re-consent then. Requesting write months before anything
write = an unused write scope on a live token, which is exactly what an
attacker inherit.

### Rotation and revocation

- Runtime key: `terraform taint aws_iam_access_key.runtime && terraform apply`.
- Full revocation: delete IAM user. Bucket untouched.
- Mail access: revoke OAuth grant with your provider. jobd hold no server-side
  session.

## 3. Known accepted risks

Stated, not hidden. Security document listing no residual risk is not describing
real system.

- **Terraform state contain runtime secret access key in plaintext.** State
  local and git-ignored. Treat `infra/terraform/*.tfstate` as credential file.
  Inherent to `aws_iam_access_key`, not specific to this module.
- **Long-lived access key exist at all.** Daemon run unattended, so it need
  non-interactive credential. Mitigated by scope (one bucket, no delete), not by
  lifetime.
- **Refused `terraform destroy` leave bucket less protected than it found it.**
  `force_destroy = false` stop bucket being deleted, but Terraform strip
  dependent resources first — public access block, versioning, and runtime IAM
  user already gone by time deletion fail. `terraform apply` restore all of it,
  but until you run it, bucket unversioned with no public-access block. See
  [`infra/terraform/storage/README.md`](infra/terraform/storage/README.md).
- **SSE-S3, not customer-managed KMS key.** AWS can technically read bucket. CMK
  would let you revoke by deleting key — at per-request cost and one more thing
  to back up. Revisit if your threat model include cloud provider.
- **OAuth tokens fall back to a 0600 file when the machine have no keyring.**
  A headless Linux container have no Secret Service, and `keyring` there
  resolve to a backend that raise on every call. Fallback is
  `~/.jobd/secrets/*.json`, mode 0600, on the mounted volume — outside the
  workspace, outside git, outside Postgres. Weaker than a keychain: any process
  running as your user can read it. Mitigated by announcing itself rather than
  downgrading silently — `jobd auth status` name the backend, and the wizard
  say it out loud before writing. Created with the mode via `os.open`, not
  chmod-ed after, so there is no window where it is world-readable.
- **Google test-mode refresh tokens expire after 7 days.** Publishing the OAuth
  app to Production would remove that, and require Google CASA verification,
  which §4 permanently rule out. Accepted: one `jobd auth gmail` per week for a
  single operator. Arguably a security *feature* — a stolen token die on its
  own.
- **LinkedIn coverage without Unipile is partial** (PRD §10). Accepted
  functional limit, not security one. But it is why archive import path exist.

## 4. Prompt-injection posture

Every ingested message is **untrusted input**. Recruiter email may contain text
engineered to manipulate model.

Defence structural, not a filter:

- Planning model hold **read-and-draft tools only**. No send-capable tool to
  call. So no instruction an email can contain cause a send.
- Every outbound action need **recorded human approval**. No configuration flag
  relax this (PRD §4).
- Injected content can therefore influence *draft text a human read before
  approving*. Nothing else.

Both properties enforced by tests, not code review — see build-guide milestones
M8 and M9. Add tool to planner toolset → those tests designed to fail loud.

**Chat panel extends this, doesn't replace it.** Every chat tool call runs on
a connection that issued `SET TRANSACTION READ ONLY` before any tool exists to
call it — verified: `CREATE TABLE`/`UPDATE`/etc. all raise
`ReadOnlySqlTransaction` on that connection, reads pass through unaffected
(`services/chat.py`). A mutation the model wants renders as a `Proposal` — the
existing page's own `<form>`, pre-filled, posting to the existing endpoint.
Nothing in the chat request path can write regardless of what a retrieved
message body says; the human's own click on a form they can read is still
the only thing that ever does.

## 5. Reporting a vulnerability

Open GitHub issue for non-sensitive findings. Anything exploitable against
running instance: report privately via GitHub Security Advisories on this repo,
not public issue.
