# ---------------------------------------------------------------------------
# Alertmanager's route to SNS.
#
# The gap this closes: the Phase 3 observability work gave the cluster real
# alert rules, and Alertmanager was deployed to evaluate them — routed nowhere.
# Alerts fired, were visible in the Prometheus UI, and reached no human. That is
# the difference between "not silent" and "paging", and only the second one is
# worth anything at 3am.
#
# SNS → email rather than Slack: fully Terraform-native, no third-party webhook
# to keep out of a public repo, no secret to leak. The honest cost, recorded
# rather than glossed: email alerts are easy to ignore and carry no
# acknowledgement. With a second person on this project that trade stops being
# acceptable.
#
# THE TOPIC ITSELF LIVES IN infra/dns/ (the persistent stack), because an email
# subscription needs a confirmation click and would otherwise need re-confirming
# after every teardown — an invisible failure gated on a manual step. Only the
# IAM role is here, because it is bound to *this* cluster's OIDC provider and
# genuinely is disposable.
# ---------------------------------------------------------------------------

# Same pattern as the Route 53 zone and the ACM certificate: this stack already
# requires the persistent stack to have been applied first, so this adds a
# dependency in kind, not a new kind of dependency. It also resolves fine during
# `terraform destroy` — unlike a Strimzi Secret lookup — precisely because the
# persistent stack is never destroyed.
data "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

locals {
  alertmanager_sa = "alertmanager"
}

# ---------------------------------------------------------------------------
# IRSA for Alertmanager. sns:Publish on the NODE role would hand it to every pod
# on the cluster; this binds it to one ServiceAccount in one namespace.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "alertmanager_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    # Without the :sub condition any pod on the cluster could assume the role,
    # which would defeat the point of using IRSA over the node role.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values = [
        "system:serviceaccount:${var.observability_namespace}:${local.alertmanager_sa}",
      ]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "alertmanager_publish" {
  statement {
    actions   = ["sns:Publish"]
    resources = [data.aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role" "alertmanager" {
  name               = "${local.name}-alertmanager"
  assume_role_policy = data.aws_iam_policy_document.alertmanager_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "alertmanager" {
  name   = "publish-alerts"
  role   = aws_iam_role.alertmanager.id
  policy = data.aws_iam_policy_document.alertmanager_publish.json
}

# ---------------------------------------------------------------------------
# The route, handed to the kube-prometheus-stack release in observability.tf.
# ---------------------------------------------------------------------------

locals {
  alertmanager_values = {
    serviceAccount = {
      create = true
      # Named explicitly so the IRSA :sub condition above is exact rather than
      # depending on how the chart derives a name from the release.
      name = local.alertmanager_sa
      annotations = {
        "eks.amazonaws.com/role-arn" = aws_iam_role.alertmanager.arn
      }
    }

    # Supplying `config` REPLACES the chart's default wholesale, including its
    # handling of Watchdog — and that matters more than it sounds. Watchdog is a
    # dead-man's switch: it fires constantly, by design, so that a monitoring
    # system which has silently died is detectable by the *absence* of its
    # alerts. Route it to SNS along with everything else and it emails forever,
    # which is the fastest possible way to train yourself to ignore this inbox.
    # It goes to a black-hole receiver instead. Same for InfoInhibitor, which is
    # plumbing rather than a condition anyone should read.
    config = {
      route = {
        # Grouping by alertname guarantees `.CommonLabels.alertname` is set,
        # which the subject template below depends on — SNS rejects a publish
        # with an empty subject, so an ungrouped alert would fail to deliver
        # rather than arrive unlabelled.
        group_by        = ["alertname", "namespace"]
        group_wait      = "30s"
        group_interval  = "5m"
        repeat_interval = "4h"
        receiver        = "sns"

        routes = [
          {
            receiver = "black-hole"
            matchers = ["alertname = Watchdog"]
          },
          {
            receiver = "black-hole"
            matchers = ["alertname = InfoInhibitor"]
          },
        ]
      }

      receivers = [
        { name = "black-hole" },
        {
          name = "sns"
          sns_configs = [{
            topic_arn = data.aws_sns_topic.alerts.arn
            # Credentials come from IRSA; only the region is needed here.
            sigv4 = { region = var.region }
            # SNS caps a subject at 100 characters and rejects newlines, so this
            # stays deliberately small. The detail belongs in the body.
            subject = "[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}"
            message = "{{ template \"sns.default.message\" . }}"
          }]
        },
      ]
    }
  }
}
