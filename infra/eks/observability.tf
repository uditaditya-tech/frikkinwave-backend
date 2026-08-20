# ---------------------------------------------------------------------------
# Observability (Phase 3).
#
# The gap this closes: **consumer lag has no signal at all.** The health checks
# added in stage 5 cover everything between `publish()` and the broker — relay
# down, relay wedged, Kafka unreachable, ACL missing. They say nothing about the
# other half. A message that reaches Kafka and is never consumed leaves the
# outbox perfectly clean, so the producer side reports success while the work
# never happens.
#
# kube-prometheus-stack rather than a hand-rolled Prometheus: Strimzi publishes
# PodMonitor resources built for this operator, so scraping is declarative
# instead of a static config that drifts. One more operator on a cluster that
# already runs Strimzi is a familiar pattern, not a new one.
# ---------------------------------------------------------------------------

resource "kubernetes_namespace_v1" "observability" {
  metadata {
    name   = var.observability_namespace
    labels = { "app.kubernetes.io/managed-by" = "terraform" }
  }
}

resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prometheus-stack"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.kube_prometheus_stack_version
  namespace  = kubernetes_namespace_v1.observability.metadata[0].name

  wait    = true
  timeout = 900

  values = [yamlencode({
    # Watch every namespace, not just this one. The whole point is scraping
    # Strimzi in `kafka` and the app in `frikkinwave`.
    prometheus = {
      prometheusSpec = {
        podMonitorSelectorNilUsesHelmValues     = false
        serviceMonitorSelectorNilUsesHelmValues = false
        ruleSelectorNilUsesHelmValues           = false
        # 7 days. This cluster is torn down between sessions, so anything longer
        # is storage paid for data that dies with the stack.
        retention = "7d"
        resources = {
          requests = { cpu = "200m", memory = "1Gi" }
          limits   = { cpu = "1000m", memory = "2Gi" }
        }
        storageSpec = {
          volumeClaimTemplate = {
            spec = {
              # gp3 — the class stage 0 created. Without it this PVC would hang
              # Pending forever, which is how that whole saga started.
              storageClassName = kubernetes_storage_class_v1.gp3.metadata[0].name
              accessModes      = ["ReadWriteOnce"]
              resources        = { requests = { storage = "20Gi" } }
            }
          }
        }
      }
    }

    grafana = {
      # No PVC. Dashboards are provisioned from ConfigMaps, so Grafana holds no
      # state worth keeping — losing the pod loses nothing.
      persistence = { enabled = false }
      resources = {
        requests = { cpu = "50m", memory = "192Mi" }
        limits   = { cpu = "300m", memory = "384Mi" }
      }
      # Any ConfigMap labelled grafana_dashboard becomes a dashboard, which is
      # what lets the event-pipeline dashboard live in the app chart next to the
      # thing it describes.
      sidecar = {
        dashboards = {
          enabled         = true
          searchNamespace = "ALL"
          label           = "grafana_dashboard"
        }
      }
      # Reachable by port-forward only. Grafana ships with a default admin
      # password and this repo is public; an Ingress here would be the AKHQ
      # mistake again, with a login screen instead of none.
      service = { type = "ClusterIP" }
    }

    # The receiver, the route and the ServiceAccount binding all come from
    # alerting.tf. Unconditional: the SNS topic is owned by the persistent stack
    # and always exists, so this stack never has to ask whether alerting is
    # configured. Whether anything is *subscribed* is that stack's business.
    alertmanager = merge(
      {
        alertmanagerSpec = {
          resources = {
            requests = { cpu = "20m", memory = "96Mi" }
            limits   = { cpu = "100m", memory = "192Mi" }
          }
        }
      },
      local.alertmanager_values,
    )

    # Node-level metrics are useful, but the kubelet/etcd/scheduler scrapes that
    # this chart enables by default do not work on EKS — the control plane is
    # managed and those endpoints are not reachable. Left on would produce
    # permanently failing targets, which trains people to ignore red.
    kubeEtcd              = { enabled = false }
    kubeControllerManager = { enabled = false }
    kubeScheduler         = { enabled = false }
    kubeProxy             = { enabled = false }
  })]

  depends_on = [
    aws_eks_node_group.main,
    kubernetes_storage_class_v1.gp3,
  ]
}
