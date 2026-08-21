data "aws_caller_identity" "current" {}

locals {
  bucket_name = var.bucket_name != "" ? var.bucket_name : "jobd-raw-${data.aws_caller_identity.current.account_id}"
}

# --- Raw message storage -----------------------------------------------------
# Source of truth for every derived artifact. Postgres is rebuildable; this is
# not. Hence versioning, no public access, and force_destroy off by default.

resource "aws_s3_bucket" "raw" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3. A customer-managed KMS key would also let us revoke access by
      # deleting the key, but it adds per-request cost and a second thing to
      # back up. Revisit if the threat model ever includes AWS-side access.
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    # Disable ACLs entirely; access is governed only by the IAM policy below.
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  # Ordering matters: versioning must be on before lifecycle rules that
  # reference noncurrent versions.
  depends_on = [aws_s3_bucket_versioning.raw]

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }
  }

  rule {
    id     = "raw-to-infrequent-access"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    # Deliberately no Glacier tier. PRD I3 makes "re-derive the world from raw"
    # a routine operation; a storage class with hours-long restore latency
    # would quietly turn that into an outage.
  }
}

# --- Runtime credential ------------------------------------------------------
# The console session running `terraform apply` is broad and short-lived.
# The credential jobd actually runs on is this one: a single principal that can
# read and write one bucket and nothing else. PRD §7.

resource "aws_iam_user" "runtime" {
  name = "jobd-runtime"
  path = "/jobd/"
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "ListOwnBucketOnly"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]

    resources = [aws_s3_bucket.raw.arn]
  }

  statement {
    sid    = "ReadWriteObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = ["${aws_s3_bucket.raw.arn}/*"]
  }

  # No s3:DeleteObject, by design. Raw messages are immutable once written;
  # content-hash keying means a re-import rewrites the same key with the same
  # bytes. Cleanup is lifecycle policy's job, or the operator's — not the
  # daemon's. This is also what makes the M1 gate's "delete is denied" check
  # meaningful rather than decorative.
}

resource "aws_iam_user_policy" "runtime" {
  name   = "jobd-runtime-s3"
  user   = aws_iam_user.runtime.name
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_iam_access_key" "runtime" {
  user = aws_iam_user.runtime.name
}
