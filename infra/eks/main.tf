# ---------------------------------------------------------------------------
# frikkinwave — EKS app stack (Phase 1: cluster foundation)
#
# A THIRD Terraform stack, independent of:
#   infra/dns/       PERSISTENT  — Route 53 zone + ACM cert. Never destroyed.
#   infra/terraform/ LEGACY      — the ECS stack. Currently NOT applied ($0).
#                                  Kept in git as a fallback; being replaced by this.
#
# Everything here is disposable: `terraform destroy` (or ./infra/scripts/eks-down.sh)
# returns the whole stack to $0. That matters — EKS bills a $0.10/hr control plane,
# so an idle cluster quietly drains AWS credits with no invoice to warn you.
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws        = { source = "hashicorp/aws", version = "~> 6.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" }
    tls        = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}

provider "aws" {
  region = var.region
}

locals {
  name = "${var.project}-${var.environment}"

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Stack       = "eks"
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

# The cluster's OIDC issuer cert thumbprint — required to create the IAM OIDC
# provider that makes IRSA (IAM Roles for Service Accounts) possible.
data "tls_certificate" "oidc" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

# Talk to the cluster's API for post-create resources (access entries, etc.).
provider "kubernetes" {
  host                   = aws_eks_cluster.main.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.main.name, "--region", var.region]
  }
}
