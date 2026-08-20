# ---------------------------------------------------------------------------
# Where alerts actually go.
#
# The gap this closes: the Phase 3 observability work gave the cluster real
# alert rules, and Alertmanager was deployed to evaluate them — routed nowhere.
# Alerts fired, were visible in the Prometheus UI, and reached no human. That is
# the difference between "not silent" and "paging", and only the second one is
# worth anything at 3am.
#
# SNS → email rather than Slack: it is fully Terraform-native, there is no
# third-party webhook to keep out of a public repo, and no secret to leak. The
# honest cost, recorded rather than glossed: email alerts are easy to ignore and
# carry no acknowledgement. If this project ever has a second person on it, that
# trade stops being acceptable.
#
# Alertmanager authenticates to SNS with IRSA — the same mechanism the load
# balancer controller uses, for the same reason: granting sns:Publish to the
# *node* role would hand it to every pod on the cluster.
# ---------------------------------------------------------------------------

locals {
  # "" means no route configured, so a fresh clone still applies cleanly.
  alerting_enabled = var.alert_email != ""

  alertmanager_sa = "alertmanager"
}

resource "aws_sns_topic" "alerts" {
  count = local.alerting_enabled ? 1 : 0

  name = "${local.name}-alerts"
  tags = local.tags
}

# NOTE: an email subscription is created in state `PendingConfirmation` and AWS
# sends a confirmation link that a human must click. Until then it accepts
# publishes and delivers NOTHING — which looks exactly like a working route.
# Terraform cannot confirm it and reports the resource as created either way, so
# `terraform apply` succeeding is not evidence that alerting works. Send a test
# alert and watch for the mail.
resource "aws_sns_topic_subscription" "alerts_email" {
  count = local.alerting_enabled ? 1 : 0

  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---------------------------------------------------------------------------
# IRSA for Alertmanager.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "alertmanager_assume" {
  count = local.alerting_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    # Bound to exactly one ServiceAccount in one namespace — without the :sub
    # condition any pod on the cluster could assume the role.
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
  count = local.alerting_enabled ? 1 : 0

  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts[0].arn]
  }
}

resource "aws_iam_role" "alertmanager" {
  count = local.alerting_enabled ? 1 : 0

  name               = "${local.name}-alertmanager"
  assume_role_policy = data.aws_iam_policy_document.alertmanager_assume[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy" "alertmanager" {
  count = local.alerting_enabled ? 1 : 0

  name   = "publish-alerts"
  role   = aws_iam_role.alertmanager[0].id
  policy = data.aws_iam_policy_document.alertmanager_publish[0].json
}

# ---------------------------------------------------------------------------
# The Alertmanager route, handed to the kube-prometheus-stack release.
#
# Kept here rather than inline in observability.tf so the whole "where do alerts
# go" story is one file: topic, subscription, IAM, route.
# ---------------------------------------------------------------------------

locals {
  # A `cond ? {...} : {}` would be the obvious shape, but Terraform requires both
  # branches of a ternary to have consistent types and rejects an object against
  # an empty one. Iterating an empty list and splatting into merge() gives a real
  # empty map when alerting is off, and never evaluates the SNS references.
  alertmanager_values = merge([for _ in(local.alerting_enabled ? [true] : []) : {
    serviceAccount = {
      create = true
      # Named explicitly so the IRSA :sub condition above is exact rather than
      # depending on how the chart derives a name from the release.
      name = local.alertmanager_sa
      annotations = {
        "eks.amazonaws.com/role-arn" = aws_iam_role.alertmanager[0].arn
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
            topic_arn = aws_sns_topic.alerts[0].arn
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
  }]...)
}
