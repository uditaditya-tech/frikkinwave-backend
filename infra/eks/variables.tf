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
