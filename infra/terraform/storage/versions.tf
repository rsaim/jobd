terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }

  # State is local for now, and .gitignore'd because it contains the runtime
  # secret access key in plaintext. Moving state into S3 would mean the bucket
  # this module creates must exist before the module can run, so that is a
  # deliberate post-v1 change, not an oversight.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}
