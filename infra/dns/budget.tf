# ---------------------------------------------------------------------------
# Cost guardrail.
#
# The failure mode this prevents: AWS credits drain with no invoice and no
# warning, so a cluster left running over a weekend can quietly consume weeks of
# budget. At ~$0.16/hr this stack burns ~$115/mo if never torn down.
# ---------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly" {
  count = var.budget_alert_email == "" ? 0 : 1

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Warn on the way up...
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # ...and again at the limit.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # Forecast catches a runaway *before* the money is gone.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
