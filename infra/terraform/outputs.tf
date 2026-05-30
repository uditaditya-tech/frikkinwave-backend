output "aws_region" {
  description = "Region everything is deployed in."
  value       = var.region
}

output "ecr_repository_url" {
  description = "Push images here (used by scripts/push-image.sh)."
  value       = aws_ecr_repository.app.repository_url
}

output "alb_dns_name" {
  description = "Public DNS name of the load balancer."
  value       = aws_lb.main.dns_name
}

output "health_check_url" {
  description = "Hit this to confirm the service is live."
  value       = "http://${aws_lb.main.dns_name}/api/health/"
}
