# ---------------------------------------------------------------------------
# Strimzi + the Kafka cluster.
#
# INFRASTRUCTURE ONLY. The application stays on Celery — Kafka runs alongside
# it and nothing produces to or consumes from it yet. That is deliberate: it
# keeps everything here reversible, and it separates "can this cluster run
# Kafka" from "does the event backbone still work", which are two failures
# nobody wants to debug at the same time. See KAFKA.md stages 3-5.
#
# Strimzi on nodes we already pay for, rather than MSK: MSK Serverless carries a
# base charge near $0.75/hr before a single byte of throughput — four times the
# cost of this entire stack.
#
# TWO helm_releases, not one, and not kubernetes_manifest. A CRD and a custom
# resource OF that CRD cannot be created by the same terraform apply:
# kubernetes_manifest validates against the API server's schema at PLAN time,
# and at plan time the CRD does not exist yet. Helm does no such lookup, so the
# operator (which installs the CRDs) and the cluster (which uses them) can
# simply be ordered with depends_on.
# ---------------------------------------------------------------------------

resource "kubernetes_namespace_v1" "kafka" {
  metadata {
    name   = var.kafka_namespace
    labels = { "app.kubernetes.io/managed-by" = "terraform" }
  }
}

resource "helm_release" "strimzi" {
  name       = "strimzi"
  repository = "https://strimzi.io/charts/"
  chart      = "strimzi-kafka-operator"
  version    = var.strimzi_chart_version
  namespace  = kubernetes_namespace_v1.kafka.metadata[0].name

  # The operator must be running and its CRDs established before the Kafka
  # resource below is applied, or that apply fails with "no matches for kind".
  wait    = true
  timeout = 600

  set = [
    # Watch only its own namespace. The alternative (watchAnyNamespace) grants
    # the operator cluster-wide RBAC over every workload type it manages, which
    # is a lot of authority for a component with exactly one tenant.
    { name = "watchNamespaces", value = "{${var.kafka_namespace}}" },
  ]

  depends_on = [aws_eks_node_group.main]
}

resource "helm_release" "kafka_cluster" {
  name      = "kafka"
  chart     = "${path.module}/../helm/kafka"
  namespace = kubernetes_namespace_v1.kafka.metadata[0].name

  # A local chart, so no repository. Terraform re-reads it on every plan; the
  # values that matter are in the chart's own values.yaml, next to the comments
  # explaining them, rather than scattered through `set` blocks here.

  # `wait` on the *Helm* objects only — helm considers a custom resource ready
  # as soon as it is accepted, because it has no idea what Kafka readiness
  # means. The brokers coming up is verified separately, after the apply.
  wait    = true
  timeout = 900

  # Storage is the hard dependency: without the gp3 class from ebs-csi.tf every
  # broker PVC hangs Pending forever and the cluster never forms.
  depends_on = [
    helm_release.strimzi,
    kubernetes_storage_class_v1.gp3,
  ]
}

# ---------------------------------------------------------------------------
# Kafka credentials, mirrored into the application namespace.
#
# Kubernetes Secrets are namespaced and cannot be mounted across namespaces. The
# Strimzi-generated credential (`frikkinwave-app`) and the cluster CA both live
# in the `kafka` namespace; the workers that run the outbox relay live in the
# app namespace. So they have to be copied.
#
# NOTE the ordering problem this creates. `helm_release.kafka_cluster` completes
# when helm has *applied* the custom resources, not when Strimzi has reconciled
# them — the User Operator creates the credential Secret a minute or two later.
# A data source alone would therefore fail on a fresh cluster with "secret not
# found", so the wait below is load-bearing, not belt-and-braces.
#
# This is a stopgap. The right answer is External Secrets Operator (already
# planned for Phase 3) or a replication controller; revisit when ESO lands.
# ---------------------------------------------------------------------------

resource "terraform_data" "wait_for_kafka_credentials" {
  triggers_replace = [helm_release.kafka_cluster.metadata.version]

  provisioner "local-exec" {
    command = <<-CMD
      kubectl wait --for=condition=Ready kafkauser/${var.kafka_app_user} \
        -n ${var.kafka_namespace} --timeout=600s
    CMD
  }

  depends_on = [helm_release.kafka_cluster]
}

data "kubernetes_secret_v1" "kafka_app_user" {
  metadata {
    name      = var.kafka_app_user
    namespace = var.kafka_namespace
  }
  depends_on = [terraform_data.wait_for_kafka_credentials]
}

data "kubernetes_secret_v1" "kafka_cluster_ca" {
  metadata {
    name      = "${var.project}-cluster-ca-cert"
    namespace = var.kafka_namespace
  }
  depends_on = [terraform_data.wait_for_kafka_credentials]
}

# The SCRAM password. Mounted as env by the worker and the relay CronJob; the
# web pods deliberately get nothing, because they only write the outbox row and
# nudge Celery. Smaller blast radius for the same functionality.
resource "kubernetes_secret_v1" "kafka_app_user_mirror" {
  metadata {
    name      = "kafka-app-user"
    namespace = var.app_namespace
  }
  data = {
    password = data.kubernetes_secret_v1.kafka_app_user.data["password"]
  }
}

# The cluster CA, so clients can verify the brokers' certificates rather than
# skipping verification — which would give encryption without authentication of
# the server, and make a man-in-the-middle trivial inside the cluster.
resource "kubernetes_secret_v1" "kafka_ca_mirror" {
  metadata {
    name      = "kafka-cluster-ca"
    namespace = var.app_namespace
  }
  data = {
    "ca.crt" = data.kubernetes_secret_v1.kafka_cluster_ca.data["ca.crt"]
  }
}
