# infra/terraform/bootstrap — administrator identity (M1)

Makes human IAM user that runs everything else. Applied **once, by account root
user**. After that, root not sign in again.

Separate from [`../storage`](../storage) on purpose: this module need root,
storage not. One module = every bucket change need root session.

## Why IAM user, not SSO

`aws login` get AWS **Management Console** session. Means sign in as IAM user
with console password. Not same as `aws sso login`.

Real SSO (`aws sso login`) need IAM Identity Center instance. That need AWS
Organizations. This account have neither:

```console
$ aws sso-admin list-instances
{ "Instances": [] }
$ aws organizations describe-organization
AWSOrganizationsNotInUseException
```

Single-operator personal account: enabling Organization just for SSO =
disproportionate. IAM user + console password + MFA + no access keys give
property that matter: **root not a working identity.** Later enable Identity
Center → replace this module with permission sets, switch to `aws sso login`.
`../storage` unchanged.

## Apply

Sign in as root once, then:

```bash
cd infra/terraform/bootstrap
terraform init
terraform plan
terraform apply
```

Get one-time password and sign-in URL:

```bash
terraform output -raw admin_initial_password
terraform output console_signin_url
```

## Then switch identity

```bash
aws logout --all
aws login --remote          # --remote: no local callback server in a container
```

Sign in as **IAM user**. Account ID, `jobd-admin`, that password. AWS force
password change at first sign-in. Verify switch worked:

```bash
aws sts get-caller-identity --query Arn --output text
# arn:aws:iam::<account>:user/admin/jobd-admin   ← not ".../root"
```

From here, `../storage` and every other module apply as this user.

## Enabling MFA

1. Enrol device: IAM console → Users → `jobd-admin` → Security credentials →
   *Assign MFA device*.
2. Only then set `require_mfa_for_admin = true` and re-apply.

Order not optional. Policy deny every action outside MFA self-service when no
MFA session present. Apply before device exist = user locked out of everything.
Recovery need root.

## State

Local, git-ignored. Contain initial console password in plaintext — treat
`*.tfstate` as credential file. After password reset at first sign-in, stored
value stale.
