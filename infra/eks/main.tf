# ---------------------------------------------------------------------------
# frikkinwave — EKS app stack (Phase 1: cluster foundation)
#
# The disposable half of a two-stack layout:
#   infra/dns/  PERSISTENT — Route 53 zone + ACM cert. Never destroyed.
#   infra/eks/  this stack — everything else.
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
    helm       = { source = "hashicorp/helm", version = "~> 3.0" }
    tls        = { source = "hashicorp/tls", version = "~> 4.0" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
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

# Talk to the cluster's API for post-create resources (namespace, secrets).
#
# Both providers authenticate with `aws eks get-token` rather than a static
# kubeconfig, so a fresh clone can apply without running eks-up.sh first — the
# token is minted from the same AWS credentials Terraform is already using.
provider "kubernetes" {
  host                   = aws_eks_cluster.main.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.main.name, "--region", var.region]
  }
}

# Used only for cluster infrastructure (the load balancer controller). The
# application chart is deployed by infra/scripts/app-deploy.sh with the helm
# CLI, so shipping a new image is a 30-second helm upgrade instead of a full
# terraform apply over the whole stack.
provider "helm" {
  kubernetes = {
    host                   = aws_eks_cluster.main.endpoint
    cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)

    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.main.name, "--region", var.region]
    }
  }
}

# The Route 53 zone and ACM cert live in the PERSISTENT infra/dns stack, so this
# stack can be destroyed and recreated without rotating nameservers or
# re-issuing the certificate. Discover them by name.
data "aws_route53_zone" "api" {
  name         = var.api_domain
  private_zone = false
}

data "aws_acm_certificate" "api" {
  domain      = var.api_domain
  statuses    = ["ISSUED"]
  most_recent = true
}
