variable "region" {
  description = "AWS region for the raw-message bucket."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = <<-EOT
    Globally unique name for the raw-message bucket. Leave empty to derive
    "jobd-raw-<account-id>", which is unique without needing a random suffix
    and stays stable across re-applies.
  EOT
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = <<-EOT
    Allow `terraform destroy` to delete a non-empty bucket. Defaults to false
    because this bucket is the source of truth for every derived artifact
    (PRD I3) — losing it is unrecoverable. Set true only to exercise the M1
    teardown gate against a bucket holding nothing you care about.
  EOT
  type        = bool
  default     = false
}

variable "noncurrent_version_retention_days" {
  description = "How long superseded object versions are kept before expiry."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Tags applied to every resource in this module."
  type        = map(string)
  default = {
    Project   = "jobd-ai"
    ManagedBy = "terraform"
  }
}
