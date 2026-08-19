variable "project" {
  description = "Project name prefix for all resources."
  type        = string
  default     = "frikkinwave"
}

variable "environment" {
  description = "Environment suffix (prod / staging)."
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region. Mumbai, matching the existing stacks."
  type        = string
  default     = "ap-south-1"
}

variable "kubernetes_version" {
  description = <<-EOT
    EKS control-plane version.

    KEEP THIS IN STANDARD SUPPORT. Once a version passes its standard-support
    date EKS silently switches to *extended support* pricing — roughly $0.60/hr
    instead of $0.10/hr, a 6x increase for an identical cluster.

    1.36 was the AWS default when this was written, with standard support until
    2027-08-02. Before changing it, check what is actually supported today:

        aws eks describe-cluster-versions --region <region> \
          --query 'clusterVersions[].{v:clusterVersion,ends:endOfStandardSupportDate}' \
          --output table
  EOT
  type        = string
  default     = "1.36"
}

# ---------------------------------------------------------------------------
# Node group sizing — the main cost lever after the control plane
# ---------------------------------------------------------------------------

variable "node_instance_type" {
  description = <<-EOT
    ARM64/Graviton to match the app image (built linux/arm64), and cheaper than
    x86. t4g.small = 2 vCPU / 2 GB. Bump to t4g.medium before adding Kafka
    (Strimzi) or a full Prometheus stack — 2 GB will not hold them.
  EOT
  type        = string
  default     = "t4g.small"
}

variable "node_desired_size" {
  description = "Nodes to run. 2 gives real multi-node scheduling; 1 halves the node cost."
  type        = number
  default     = 2
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 3
}

# ---------------------------------------------------------------------------
# Cost guardrail
# ---------------------------------------------------------------------------

variable "budget_limit_usd" {
  description = <<-EOT
    Monthly spend that triggers an alert. AWS credits are consumed silently —
    there is no invoice to notice — so this alarm is the only early warning
    that a forgotten cluster is burning them.
  EOT
  type        = number
  default     = 20
}

variable "budget_alert_email" {
  description = "Where budget alerts go. Set to \"\" to skip creating the budget."
  type        = string
  default     = "udit.aditya.tech@gmail.com"
}

variable "console_admin_principal_arn" {
  description = <<-EOT
    An ADDITIONAL IAM principal granted cluster-admin, so the AWS console's EKS
    "Resources" tab lists pods/deployments instead of an access error.

    Leave empty unless you browse the console as a *different* identity than the
    one running terraform. The cluster creator is already granted admin by
    bootstrap_cluster_creator_admin_permissions, and passing that same principal
    here fails with ResourceInUseException (the entry already exists).
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Phase 2 — application infrastructure
# ---------------------------------------------------------------------------

variable "app_namespace" {
  description = "Kubernetes namespace the application is deployed into."
  type        = string
  default     = "frikkinwave"
}

variable "api_domain" {
  description = "Public hostname for the backend API. Must match the ACM cert in infra/dns."
  type        = string
  default     = "api.frikkinwave.com"
}

variable "lb_controller_chart_version" {
  description = <<-EOT
    Chart version for aws-load-balancer-controller.

    Note the numbering discontinuity: chart 1.x tracked controller 2.x, then the
    project realigned so chart 3.x == controller 3.x. Do not read "3.5.0" as a
    jump of two majors from 1.13.

    The vendored IAM policy in policies/ must match the controller version. It
    happens to be byte-identical between 2.13 and 3.5, but re-check on upgrade:
    a missing permission shows up as an Ingress that never gets an address and
    an obscure AccessDenied buried in the controller's logs.
  EOT
  type        = string
  default     = "3.5.0"
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

variable "db_snapshot_identifier" {
  description = <<-EOT
    Restore RDS from this snapshot when the instance is first created. Empty
    means a fresh, empty database (which then needs migrate + seed, and re-runs
    the OpenAI embedding pipeline at real cost).

    Defaults to the final snapshot taken when the ECS stack was torn down on
    2026-06-10: reference data, the real profiles (jazzcat, udit94), and the
    full demo-* Phase 5 dataset (30 musicians, 870 follows, 720 reviews).

    Verify it still exists before an apply:
      aws rds describe-db-snapshots --region ap-south-1 --snapshot-type manual
  EOT
  type        = string
  default     = "frikkinwave-prod-final-528299d1"
}

variable "db_name" {
  description = "Database name. Ignored on a snapshot restore — the snapshot carries its own."
  type        = string
  default     = "frikkinwave"
}

variable "db_username" {
  description = "Master username. Must match the snapshot's when restoring."
  type        = string
  default     = "frikkinwave"
}

variable "db_engine_version" {
  description = "Postgres version. The restore snapshot is 16.13; do not set this below it."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "db.t4g.micro is the cheapest Graviton option (~$12/mo)."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Storage in GB. Must be >= the snapshot's 20 GB."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = "Automated backup retention. 0 disables backups."
  type        = number
  default     = 1
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. false (default) retains the data for the next apply."
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Block terraform destroy until disabled. false = free to cycle."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Application secrets — set in terraform.tfvars (git-ignored)
# ---------------------------------------------------------------------------

variable "django_secret_key" {
  description = "DJANGO_SECRET_KEY for the running pods."
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OPENAI_API_KEY for embeddings/blurbs/coach. Empty disables AI calls (features degrade, nothing 500s)."
  type        = string
  sensitive   = true
  default     = ""
}
