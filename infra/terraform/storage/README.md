# infra/terraform/storage — raw storage foundation (M1)

Makes the one thing jobd-ai cannot rebuild: S3 bucket holding raw messages,
plus scoped IAM principal daemon runs as.

Everything else derived and reproducible (PRD I3). This module the exception.
That is why `force_destroy` default `false`.

## What it creates

| Resource | Notes |
|---|---|
| S3 bucket `jobd-raw-<account-id>` | Versioned, SSE-S3 encrypted, all public access blocked, ACLs disabled |
| Lifecycle rules | Abort stale multipart uploads (7d); expire noncurrent versions (90d); `raw/` → STANDARD_IA at 90d. No Glacier — see `main.tf` |
| IAM user `/jobd/jobd-runtime` | Read/write on that bucket only. No delete, no wildcards |
| Access key | Daemon long-lived credential. Emitted as sensitive output |

## Prerequisites

Apply [`../bootstrap`](../bootstrap) first. Then sign in as admin IAM user it
makes. That identity is **bootstrap** credential. Distinct from runtime
credential this module produce — see [`SECURITY.md`](../../../SECURITY.md) §2
for why split matter.

```bash
aws login            # local machine with a browser
aws login --remote   # Codespaces / SSH — no local callback server
```

`--remote` matter in container: redirect flow start callback listener on
`127.0.0.1` *inside* container. Browser AWS redirects run on your laptop.
Callback never arrive.

Confirm not root before apply:

```bash
aws sts get-caller-identity --query Arn --output text   # must not end in ":root"
```

## Apply

```bash
cd infra/terraform/storage
terraform init
terraform plan          # read-only; review before applying
terraform apply
```

Override derived bucket name:

```bash
terraform apply -var 'bucket_name=my-jobd-bucket'
```

## Wire up runtime credential

Write generated key into `jobd` profile on persistent volume. Survive container
rebuild, never enter workspace:

```bash
mkdir -p ~/.jobd/aws && chmod 700 ~/.jobd/aws
{
  echo '[jobd]'
  echo "aws_access_key_id = $(terraform output -raw runtime_access_key_id)"
  echo "aws_secret_access_key = $(terraform output -raw runtime_secret_access_key)"
} >> ~/.jobd/aws/credentials
chmod 600 ~/.jobd/aws/credentials
```

`~/.aws` is symlink to `~/.jobd/aws` (made by `postCreateCommand`). So this is
ordinary credentials file. Just happen to live on volume that survive container
rebuild.

## Verifying M1 gate

Gate not "bucket exists". Gate is runtime credential genuinely confined.
Scoping only real once you watch it deny something:

```bash
BUCKET=$(terraform output -raw bucket_name)

# 1. Roundtrip succeeds with the scoped profile
echo hello | AWS_PROFILE=jobd aws s3 cp - "s3://$BUCKET/raw/smoke-test.txt"
AWS_PROFILE=jobd aws s3 cp "s3://$BUCKET/raw/smoke-test.txt" -

# 2. Delete is denied — raw is immutable to the daemon
AWS_PROFILE=jobd aws s3 rm "s3://$BUCKET/raw/smoke-test.txt"   # expect AccessDenied

# 3. Any other bucket is denied
AWS_PROFILE=jobd aws s3 ls s3://some-other-bucket               # expect AccessDenied
```

Steps 2 and 3 must **fail**. Either succeed = policy broader than this README
claim, and SECURITY.md §2 wrong.

## Teardown

```bash
terraform destroy   # fails if the bucket holds objects OR old versions
```

Guard deliberate. Force past it on real bucket = delete source of truth for
every derived artifact. No rebuild recover it.

### Forcing destroy take two commands, not one

`terraform destroy -var 'force_destroy=true'` **not work**. Fail in way that
look like flag ignored:

```
Error: deleting S3 Bucket (...): api error BucketNotEmpty
```

`force_destroy` is attribute of bucket resource. On destroy, provider read it
from **prior state**, not from variable you just passed. Setting on destroy
command too late — never reach delete path. Write into state with apply first:

```bash
terraform apply   -var 'force_destroy=true'   # updates state; no AWS API call
terraform destroy -var 'force_destroy=true'
```

Also: bucket showing zero objects can still be non-empty. Versioning on, so
`aws s3 rm` write delete markers, remove nothing. `list-objects-v2` then return
nothing while old versions and markers remain. `DeleteBucket` count them. Check:

```bash
aws s3api list-object-versions --bucket "$BUCKET" \
  --query '{versions: length(Versions || `[]`), markers: length(DeleteMarkers || `[]`)}'
```

After re-apply, confirm `force_destroy` back to `false` in state
(`terraform state show aws_s3_bucket.raw | grep force_destroy`). Leave it `true`
= guard disarmed permanently and silently.

### Failed destroy is not no-op

`force_destroy = false` protect bucket **contents**, not **configuration**.
Verified on live bucket: terraform destroy delete resources in dependency
order. By time it reach `aws_s3_bucket.raw` and get `BucketNotEmpty`, it already
tore down everything depending on bucket. Observed end state:

| | Before | After the "safe" failure |
|---|---|---|
| Public access block | all four true | **removed** |
| Versioning | Enabled | **Suspended** |
| `jobd-runtime` user and key | present | **deleted** |

Objects survive. Bucket left publicly-blockable and unversioned. Daemon
credential gone. Recovery = `terraform apply` — restore every setting, objects
untouched — but it issue **new access key**. Rewrite `~/.jobd/aws/credentials`
after (see above), or daemon authenticate with key that no longer exist.

Refused destroy ≠ "nothing happened."

## State

Local, git-ignored. Contain runtime secret access key in plaintext. Treat
`*.tfstate` as credential file. Move state into S3 = bucket must exist before
this module run. So state stay local for now.
