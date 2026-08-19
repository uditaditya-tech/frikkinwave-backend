output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "cluster_version" {
  value = aws_eks_cluster.main.version
}

output "oidc_provider_arn" {
  description = "Trust anchor for IRSA roles (Phase 3)."
  value       = aws_iam_openid_connect_provider.cluster.arn
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "aws_region" {
  value = var.region
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster."
  value       = "aws eks update-kubeconfig --name ${aws_eks_cluster.main.name} --region ${var.region}"
}

output "console_url" {
  description = "EKS console — Resources tab lists pods/deployments."
  value       = "https://${var.region}.console.aws.amazon.com/eks/clusters/${aws_eks_cluster.main.name}?region=${var.region}"
}

# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "Push target for the app image (linux/arm64)."
  value       = aws_ecr_repository.app.repository_url
}

output "db_endpoint" {
  description = "RDS address. Private to the VPC — not reachable from a laptop."
  value       = aws_db_instance.main.address
}

output "app_namespace" {
  value = var.app_namespace
}

output "app_secret_name" {
  description = "Kubernetes Secret the chart mounts via envFrom."
  value       = kubernetes_secret.app.metadata[0].name
}

output "api_domain" {
  value = var.api_domain
}

output "acm_certificate_arn" {
  description = "Cert from the persistent dns stack, attached to the ALB by the Ingress."
  value       = data.aws_acm_certificate.api.arn
}

output "route53_zone_id" {
  value = data.aws_route53_zone.api.zone_id
}
