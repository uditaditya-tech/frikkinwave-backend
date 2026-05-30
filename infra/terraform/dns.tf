# Route 53 hosted zone for the api subdomain only. The apex (frikkinwave.com)
# stays wherever it is managed (Vercel/registrar) — we delegate just `api` here
# by adding this zone's nameservers as NS records at the parent.
resource "aws_route53_zone" "api" {
  name = var.api_domain

  tags = { Name = "${local.name}-zone" }
}

# TLS certificate for the api subdomain, validated via DNS.
resource "aws_acm_certificate" "api" {
  domain_name       = var.api_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cert" }
}

# The CNAME record ACM looks for to prove we control the domain.
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.api.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = aws_route53_zone.api.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

# Blocks until ACM reports the certificate as issued (needs the NS delegation
# to be in place at the parent so the validation CNAME resolves publicly).
resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# api.frikkinwave.com → ALB (alias A record, no charge, no CNAME-at-apex issues).
resource "aws_route53_record" "api_alias" {
  zone_id = aws_route53_zone.api.zone_id
  name    = var.api_domain
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}
