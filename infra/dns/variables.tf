variable "region" {
  description = "AWS region. Must match the app stack."
  type        = string
  default     = "ap-south-1" # Mumbai
}

variable "project" {
  description = "Project name, used as a resource name prefix. Must match the app stack."
  type        = string
  default     = "frikkinwave"
}

variable "environment" {
  description = "Environment name, used as a resource name prefix. Must match the app stack."
  type        = string
  default     = "prod"
}

variable "api_domain" {
  description = "Public hostname for the backend API."
  type        = string
  default     = "api.frikkinwave.com"
}

# ---------------------------------------------------------------------------
# Cost and alerting. Persistent on purpose — see main.tf.
# ---------------------------------------------------------------------------

variable "alert_email" {
  description = <<-EOT
    Where Prometheus alerts are emailed, via SNS. Set to "" to create the topic
    with no subscriber, which is what the EKS stack did before this moved:
    alerts evaluate and are visible, and nothing pages.

    NOT a secret — that is the point of SNS over a Slack webhook, in a public
    repo. Requires a one-time confirmation click after the first apply.
  EOT
  type        = string
  default     = "udit.aditya.tech@gmail.com"
}

variable "budget_alert_email" {
  description = "Where budget alerts go. Set to \"\" to skip creating the budget."
  type        = string
  default     = "udit.aditya.tech@gmail.com"
}

variable "budget_limit_usd" {
  description = <<-EOT
    Monthly spend that triggers an alert. AWS credits are consumed silently —
    there is no invoice to notice — so this alarm is the only early warning
    that a forgotten cluster is burning them.
  EOT
  type        = number
  default     = 20
}
