# ElastiCache Redis — the Celery broker + result backend for Phase 2.
# Single-node, cluster-mode-off, in the private subnets (no public reachability);
# only the Fargate tasks SG may reach it on 6379. The web container emits tasks
# to it (transaction.on_commit .delay()), the worker service consumes them.

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name}-redis-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${local.name}-redis-subnet" }
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis-sg"
  description = "ElastiCache Redis: accept traffic only from the ECS tasks."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from tasks only"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-redis-sg" }
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${local.name}-redis"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]

  tags = { Name = "${local.name}-redis" }
}

# redis://<endpoint>:6379/0 — Celery broker URL, injected as a plain env var
# (it carries no credentials; the SG is the access control). Single-node
# clusters expose the address via cache_nodes[0].
locals {
  redis_url = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
}
