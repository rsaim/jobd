# infra/terraform

Two root modules. Split by **who must be signed in to apply**.

| Module | Applied by | Frequency | Creates |
|---|---|---|---|
| [`bootstrap/`](bootstrap) | Account **root** | Once, ever | `jobd-admin` IAM user. Runs everything else |
| [`storage/`](storage) | `jobd-admin` | Whenever storage change | Raw-message S3 bucket + scoped `jobd-runtime` principal |

Merge them = root session needed for every routine bucket change. Defeats point
of admin user.

## Order

```bash
cd bootstrap && terraform init && terraform apply    # as root, once
terraform output -raw admin_initial_password         # sign in, change password
aws logout --all && aws login --remote               # now you are jobd-admin

cd ../storage && terraform init && terraform apply   # as jobd-admin
```

## Three credentials, three blast radii

Internalise this part — PRD §7 and [`SECURITY.md`](../../SECURITY.md) §2:

1. **Root** — unscoped, unrevocable. Signs in once, make `jobd-admin`, never again.
2. **`jobd-admin`** — administrator, console password, MFA, *no access keys*. Run Terraform by hand.
3. **`jobd-runtime`** — one bucket, read/write, no delete, no wildcards. Daemon run as this. Only long-lived credential in system.

Each made by tier above. None made by hand.

## State

Local, git-ignored, both modules. `bootstrap` state hold admin initial console
password. `storage` state hold runtime secret access key. Both credential files
— see per-module READMEs.
