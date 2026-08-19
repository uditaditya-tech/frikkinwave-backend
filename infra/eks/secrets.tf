# ---------------------------------------------------------------------------
# Runtime secrets.
#
# Terraform is the single source of truth; it projects the same values to two
# places, for two different consumers:
#
#   Kubernetes Secret  — what the pods actually read today (envFrom secretRef).
#   SSM Parameter Store — not read by anything yet. Phase 3 installs External
#     Secrets Operator, which syncs SSM -> Kubernetes Secret via IRSA and takes
#     over from the resource below. Writing them now makes that phase purely
#     additive, and SSM standard parameters are free.
#
# DATABASE_URL is assembled here rather than in the chart so the generated RDS
# password never leaves Terraform's state boundary into Helm values.
# ---------------------------------------------------------------------------

locals {
  database_url = "postgres://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
}

resource "aws_ssm_parameter" "django_secret_key" {
  name  = "/${local.name}/DJANGO_SECRET_KEY"
  type  = "SecureString"
  value = var.django_secret_key
  tags  = merge(local.tags, { Name = "${local.name}-django-secret-key" })
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/${local.name}/DATABASE_URL"
  type  = "SecureString"
  value = local.database_url
  tags  = merge(local.tags, { Name = "${local.name}-database-url" })
}

# Empty is allowed — the app treats "no key" and "API down" identically and
# degrades (search -> [], compatibility -> 503, coach -> null tip).
resource "aws_ssm_parameter" "openai_api_key" {
  name  = "/${local.name}/OPENAI_API_KEY"
  type  = "SecureString"
  value = var.openai_api_key
  tags  = merge(local.tags, { Name = "${local.name}-openai-api-key" })
}

# ---------------------------------------------------------------------------
# The namespace and Secret the app chart deploys into.
#
# Deliberately owned by Terraform, not by the Helm chart: the chart is deployed
# and re-deployed on every image push, and secrets should not travel through
# `helm upgrade --set` (they would land in the release's stored manifest and in
# shell history). The chart only references this Secret by name.
# ---------------------------------------------------------------------------

resource "kubernetes_namespace" "app" {
  metadata {
    name = var.app_namespace
  }

  depends_on = [aws_eks_node_group.main]
}

resource "kubernetes_secret" "app" {
  metadata {
    name      = "${local.name}-secrets"
    namespace = kubernetes_namespace.app.metadata[0].name
  }

  # Keys match the env var names the app reads, so the chart can mount the
  # whole Secret with a single envFrom.secretRef.
  data = {
    DJANGO_SECRET_KEY = var.django_secret_key
    DATABASE_URL      = local.database_url
    OPENAI_API_KEY    = var.openai_api_key
  }

  type = "Opaque"
}
