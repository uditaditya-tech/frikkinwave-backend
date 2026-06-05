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
  description = "Hit this to confirm the service is live (raw ALB, HTTP)."
  value       = "http://${aws_lb.main.dns_name}/api/health/"
}

output "api_url" {
  description = "Public HTTPS health check once DNS + cert are live."
  value       = "https://${var.api_domain}/api/health/"
}

output "rds_endpoint" {
  description = "RDS instance hostname (not internet-reachable)."
  value       = aws_db_instance.main.address
}

output "redis_endpoint" {
  description = "ElastiCache Redis hostname (Celery broker; not internet-reachable)."
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "db_final_snapshot_name" {
  description = "Snapshot name `terraform destroy` will create for data retention (unless db_skip_final_snapshot=true)."
  value       = var.db_skip_final_snapshot ? "(skipped)" : "${local.name}-final-${random_id.final_snapshot.hex}"
}

output "worker_ecs_service" {
  description = "Name of the Celery worker ECS service."
  value       = aws_ecs_service.worker.name
}

output "worker_task_family" {
  description = "Task definition family for the Celery worker."
  value       = aws_ecs_task_definition.worker.family
}

# --- Used by scripts/run-migrations.sh ------------------------------------

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_task_family" {
  value = aws_ecs_task_definition.app.family
}

output "ecs_container_name" {
  value = local.name
}

output "public_subnet_ids" {
  description = "Comma-joined public subnet IDs for one-off task networking."
  value       = join(",", aws_subnet.public[*].id)
}

output "tasks_security_group_id" {
  value = aws_security_group.tasks.id
}
