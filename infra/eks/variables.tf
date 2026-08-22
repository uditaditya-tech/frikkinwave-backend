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

variable "node_instance_types" {
  description = <<-EOT
    Candidate instance types, in preference order. ARM64/Graviton to match the
    app image (built linux/arm64) and cheaper than x86.

    A LIST because a single type is stranded when that type is short. But the
    list only helps if the entries are in DIFFERENT CAPACITY POOLS, and the
    previous list — ["t4g.small", "t4g.medium"], later ["t4g.medium",
    "t4g.large"] — was two sizes of one family.

    That failed on 2026-08-20, when t4g was short across all three ap-south-1
    AZs: 40 consecutive launch attempts, every one t4g.medium, and the node group
    sat in CREATING for 20+ minutes with an EKS control plane billing behind it.
    Adding m7g.large — a different family, not just a different size — brought
    the cluster up immediately, and it is what the ASG actually chose for two of
    the three nodes.

    Diagnose a stuck node group with the ASG's activity log, which names both the
    type and the AZ:

        aws autoscaling describe-scaling-activities \
          --auto-scaling-group-name <asg> --region ap-south-1 \
          --query 'Activities[].StatusMessage'

    Prices (ap-south-1): t4g.large $0.0448/hr, m7g.large $0.0808/hr,
    t4g.medium $0.0224/hr. Three large nodes cost roughly $0.13-0.24/hr against
    $0.067 for three mediums — the large sizes lead because Phase 3's Prometheus
    wants the headroom, not only for capacity reasons.
  EOT
  type        = list(string)
  default     = ["t4g.large", "m7g.large", "t4g.medium"]
}

variable "node_desired_size" {
  description = <<-EOT
    Nodes to run. 3 so each Kafka broker gets its own node — the brokers use a
    hard topology spread across hostname, so a fourth broker would sit Pending
    and a third node is what makes the spread satisfiable.

    A trap worth knowing: aws_eks_node_group has
    `ignore_changes = [scaling_config[0].desired_size]`, so raising this alone
    does NOTHING to a running group. It takes effect here only because
    instance_types changed in the same commit, and that attribute forces
    replacement — the replacement group is built fresh from config, ignore_changes
    having no say over a create. Change this on its own and it silently no-ops.
  EOT
  type        = number
  default     = 3
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  description = "Headroom above desired for a rolling node replacement."
  type        = number
  default     = 4
}

# ---------------------------------------------------------------------------
# Cost guardrail
# ---------------------------------------------------------------------------

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
    Pin the restore to a SPECIFIC snapshot. Normally leave empty — the newest
    snapshot for this instance is discovered automatically (see rds.tf), which
    is what the last teardown preserved.

    Set this only to restore a deliberately older point in time. Note it is
    ignored once the instance exists (lifecycle.ignore_changes), because
    changing it on a live database would destroy and recreate it.
  EOT
  type        = string
  default     = ""
}

variable "db_restore_from_latest_snapshot" {
  description = <<-EOT
    Restore from the most recent snapshot of this instance. True is almost
    always right: teardown takes a final snapshot, so this returns the state you
    left behind.

    Set false for a genuinely empty database — including the first apply in a
    brand-new AWS account, where no snapshot exists yet and the lookup would
    otherwise fail.
  EOT
  type        = bool
  default     = true
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

# ---------------------------------------------------------------------------
# OpenSearch (the search index)
# ---------------------------------------------------------------------------

variable "opensearch_engine_version" {
  description = "Managed engine version. Keep the major aligned with opensearch-py in requirements/base.txt."
  type        = string
  default     = "OpenSearch_3.7"
}

variable "opensearch_instance_type" {
  description = "Domain instance type. t3.small.search is the cheapest that supports encryption at rest, which fine-grained access control requires."
  type        = string
  default     = "t3.small.search"
}

variable "opensearch_volume_size" {
  description = "EBS volume size (GiB) per node. The index is a few hundred small documents; 10 is already generous."
  type        = number
  default     = 10
}

variable "opensearch_username" {
  description = "Fine-grained access control master user. The password is generated and never leaves Terraform state into git."
  type        = string
  default     = "frikkinwave"
}

# ---------------------------------------------------------------------------
# Kafka (see KAFKA.md)
# ---------------------------------------------------------------------------

variable "kafka_namespace" {
  description = "Namespace for the Strimzi operator and the Kafka cluster."
  type        = string
  default     = "kafka"
}

variable "strimzi_chart_version" {
  description = <<-EOT
    Chart version for strimzi-kafka-operator. Chart and app version track each
    other exactly, so 1.1.0 here means Strimzi 1.1.0.

    Note the numbering: the project went 0.51.0 -> 1.0.0, so "1.1.0" is one
    minor after the first stable, not a decade of majors.

    Two things are version-sensitive and will bite on an upgrade:

      1. API VERSION. 1.x serves only kafka.strimzi.io/v1; v1beta2 is gone. The
         manifests in infra/helm/kafka are written for v1 and a test asserts it.
      2. SUPPORTED KAFKA VERSIONS. Each operator release ships images for a
         short list only — 1.1.0 has 4.2.0, 4.2.1, 4.3.0. Read the list before
         changing kafkaVersion in the chart:

             helm template strimzi strimzi/strimzi-kafka-operator \
               --version <v> | grep -A6 STRIMZI_KAFKA_IMAGES
  EOT
  type        = string
  default     = "1.1.0"
}

variable "kafka_app_user" {
  description = <<-EOT
    The KafkaUser the application authenticates as. Must match `appUser` in
    infra/helm/kafka/values.yaml — Terraform mirrors this user's generated Secret
    into the app namespace, and a mismatch fails the apply at the wait step
    rather than silently shipping without credentials.
  EOT
  type        = string
  default     = "frikkinwave-app"
}

# ---------------------------------------------------------------------------
# Observability (Phase 3)
# ---------------------------------------------------------------------------

variable "observability_namespace" {
  description = "Namespace for Prometheus, Grafana and Alertmanager."
  type        = string
  default     = "observability"
}

variable "kube_prometheus_stack_version" {
  description = <<-EOT
    Chart version for kube-prometheus-stack.

    Note it bundles the Prometheus Operator's CRDs. Upgrading a major version
    can require applying CRDs by hand first — helm does not upgrade CRDs it
    installed as part of a chart. Read the chart's release notes before bumping.
  EOT
  type        = string
  default     = "88.5.0"
}
