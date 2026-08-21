output "admin_user_name" {
  description = "Name of the administrator IAM user."
  value       = aws_iam_user.admin.name
}

output "admin_user_arn" {
  description = "ARN of the administrator IAM user."
  value       = aws_iam_user.admin.arn
}

output "console_signin_url" {
  description = "Account-specific console sign-in URL for IAM users."
  value       = "https://${data.aws_caller_identity.current.account_id}.signin.aws.amazon.com/console"
}

output "admin_initial_password" {
  description = <<-EOT
    One-time console password. Read it with
    `terraform output -raw admin_initial_password`, sign in once, and change it
    immediately — AWS forces a reset on first sign-in.

    This value is stored in plaintext in local state, which is why *.tfstate is
    git-ignored. After you have reset the password the stored value is stale and
    harmless, but rotate it anyway if state was ever exposed.
  EOT
  value       = aws_iam_user_login_profile.admin.password
  sensitive   = true
}
