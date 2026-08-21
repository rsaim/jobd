terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }

  # Local state, git-ignored. This module's state contains the admin user's
  # initial console password, so treat it as a credential file.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}
