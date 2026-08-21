data "aws_caller_identity" "current" {}

# --- Human administrator -----------------------------------------------------
# Applied once, by the account root user. Everything afterwards — including the
# storage module — runs as this user instead, so root is never a working
# identity. Root cannot be scoped, restricted by policy, or revoked without
# changing the account password, which is exactly why it should sign in once
# and then not again.

resource "aws_iam_user" "admin" {
  name = var.admin_user_name
  path = "/admin/"
}

resource "aws_iam_user_policy_attachment" "admin" {
  user       = aws_iam_user.admin.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# Console password, because `aws login` acquires a *console* session — there is
# no IAM Identity Center instance on this account, so `aws sso login` is not an
# option (see README). Deliberately no access keys: this identity is used
# interactively and should not have a long-lived credential lying around.
resource "aws_iam_user_login_profile" "admin" {
  user                    = aws_iam_user.admin.name
  password_length         = 32
  password_reset_required = true

  lifecycle {
    # Once the human resets the password at first sign-in, the value in state is
    # stale by design. Without this, every subsequent apply would reset it.
    ignore_changes = [
      password_length,
      password_reset_required,
    ]
  }
}

# --- Optional MFA enforcement ------------------------------------------------
# Off by default. Enabling it before a device is enrolled locks the user out of
# everything except MFA self-service, which is recoverable only via root.

data "aws_iam_policy_document" "require_mfa" {
  count = var.require_mfa_for_admin ? 1 : 0

  # Let the user manage their own MFA device without an MFA session, otherwise
  # enrolment is impossible.
  statement {
    sid    = "AllowManageOwnMFA"
    effect = "Allow"

    actions = [
      "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:ResyncMFADevice",
      "iam:ListMFADevices",
      "iam:ListVirtualMFADevices",
      "iam:DeactivateMFADevice",
      "iam:DeleteVirtualMFADevice",
      "iam:ChangePassword",
      "iam:GetUser",
    ]

    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/&{aws:username}",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:mfa/&{aws:username}",
    ]
  }

  statement {
    sid    = "DenyEverythingElseWithoutMFA"
    effect = "Deny"
    not_actions = [
      "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice",
      "iam:ResyncMFADevice",
      "iam:ListMFADevices",
      "iam:ListVirtualMFADevices",
      "iam:DeactivateMFADevice",
      "iam:DeleteVirtualMFADevice",
      "iam:ChangePassword",
      "iam:GetUser",
      "sts:GetSessionToken",
    ]
    resources = ["*"]

    condition {
      test     = "BoolIfExists"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["false"]
    }
  }
}

resource "aws_iam_user_policy" "require_mfa" {
  count = var.require_mfa_for_admin ? 1 : 0

  name   = "require-mfa"
  user   = aws_iam_user.admin.name
  policy = data.aws_iam_policy_document.require_mfa[0].json
}
