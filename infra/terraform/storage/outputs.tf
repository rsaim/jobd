output "bucket_name" {
  description = "Name of the raw-message bucket."
  value       = aws_s3_bucket.raw.id
}

output "bucket_arn" {
  description = "ARN of the raw-message bucket."
  value       = aws_s3_bucket.raw.arn
}

output "region" {
  description = "Region the bucket lives in."
  value       = var.region
}

output "runtime_access_key_id" {
  description = "Access key ID for the scoped jobd runtime principal."
  value       = aws_iam_access_key.runtime.id
}

output "runtime_secret_access_key" {
  description = <<-EOT
    Secret access key for the scoped jobd runtime principal. Read it with
    `terraform output -raw runtime_secret_access_key`; see the module README
    for the one-liner that writes it into the jobd profile on the persistent
    volume. It is also stored in plaintext in local state, which is why
    *.tfstate is git-ignored.
  EOT
  value       = aws_iam_access_key.runtime.secret
  sensitive   = true
}
