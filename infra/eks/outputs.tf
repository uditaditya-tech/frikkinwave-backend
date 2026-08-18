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
