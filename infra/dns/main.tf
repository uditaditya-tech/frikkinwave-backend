terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state — this is the PERSISTENT layer. Never `terraform destroy` it.
  #
  # It holds everything that must outlive a teardown of the app stack, which is
  # no longer only DNS:
  #   - the Route 53 zone (whose nameservers GoDaddy delegates to) + ACM cert
  #   - the budget alarm, which matters MOST after teardown — orphaned resources
  #     bill once the cluster is gone, and credits expire December 2026
  #   - the SNS alert topic, because an email subscription delivers nothing
  #     until a human clicks a confirmation link, and a topic destroyed every
  #     session would need re-confirming every session
  #
  # ../eks discovers all of these via data sources and must be applied second.
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
  name = "${var.project}-${var.environment}"
}
