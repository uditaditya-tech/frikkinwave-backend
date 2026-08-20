# ---------------------------------------------------------------------------
# Where operational alerts go.
#
# The topic lives in the PERSISTENT stack for one reason: an SNS email
# subscription is created `PendingConfirmation` and delivers NOTHING until a
# human clicks the link AWS mails. Held in the disposable stack, every teardown
# destroyed the topic and its subscription, so every rebuild needed that click
# again — a silent failure gated on a manual step that recurs every session.
# Here it is confirmed once and survives.
#
# The topic is created unconditionally. It costs nothing unsubscribed, and one
# fewer conditional in the EKS stack is worth more than the zero dollars saved:
# the app stack can wire Alertmanager to it without knowing whether alerting is
# configured.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

# NOTE: Terraform reports this as created whether or not it is confirmed, so
# `terraform apply` succeeding is NOT evidence that alerting works. eks-up.sh
# checks for PendingConfirmation on every bring-up as a backstop.
resource "aws_sns_topic_subscription" "alerts_email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
