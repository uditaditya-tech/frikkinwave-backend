terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State is LOCAL for now (terraform.tfstate on disk, git-ignored).
  # Migrate to an S3 backend + DynamoDB lock table once the account is bootstrapped:
  #   backend "s3" { bucket = "...", key = "frikkinwave/prod.tfstate", region = "...", dynamodb_table = "..." }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  # Common name prefix, e.g. "frikkinwave-prod"
  name = "${var.project}-${var.environment}"
}
