variable "region" {
  description = "AWS region. IAM is global; this only sets the provider endpoint."
  type        = string
  default     = "us-east-1"
}

variable "admin_user_name" {
  description = "Name of the human administrator IAM user used to run the storage module."
  type        = string
  default     = "jobd-admin"
}

variable "require_mfa_for_admin" {
  description = <<-EOT
    Attach a policy denying every action unless the session is MFA-authenticated,
    while still permitting the user to enrol their own MFA device. Leave false
    until you have enrolled a device, or you will lock the user out of everything
    except MFA self-management.
  EOT
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to every resource in this module."
  type        = map(string)
  default = {
    Project   = "jobd-ai"
    ManagedBy = "terraform"
    Module    = "bootstrap"
  }
}
