output "api_nameservers" {
  description = "Add these as NS records for 'api' at the parent domain's DNS (one-time delegation)."
  value       = aws_route53_zone.api.name_servers
}

output "zone_id" {
  description = "Route 53 hosted zone ID for the api subdomain."
  value       = aws_route53_zone.api.zone_id
}

output "certificate_arn" {
  description = "ARN of the validated ACM certificate."
  value       = aws_acm_certificate_validation.api.certificate_arn
}
