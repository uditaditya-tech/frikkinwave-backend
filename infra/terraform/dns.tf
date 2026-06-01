# The Route 53 zone and ACM cert live in the PERSISTENT stack (../dns) so this
# stack can be destroyed/recreated without rotating nameservers or re-issuing the
# cert. We discover them here by name/domain.
data "aws_route53_zone" "api" {
  name         = var.api_domain
  private_zone = false
}

data "aws_acm_certificate" "api" {
  domain      = var.api_domain
  statuses    = ["ISSUED"]
  most_recent = true
}

# api.frikkinwave.com → ALB (alias A record). Stays in the app stack because it
# targets the ALB, which is recreated on each apply; the alias auto-updates.
resource "aws_route53_record" "api_alias" {
  zone_id = data.aws_route53_zone.api.zone_id
  name    = var.api_domain
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}
