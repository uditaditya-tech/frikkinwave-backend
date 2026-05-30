resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256" # 0.25 vCPU — smallest Fargate size
  memory                   = "512" # 0.5 GB
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  # ARM64 (Graviton) — matches the arm64 image built on the dev Mac and is
  # marginally cheaper than x86. The push script builds --platform linux/arm64.
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = local.name
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.production" },
        { name = "ALLOWED_HOSTS", value = aws_lb.main.dns_name },
        { name = "CORS_ALLOWED_ORIGINS", value = var.cors_allowed_origins },
        { name = "WEB_CONCURRENCY", value = "3" },
      ]

      # Injected from SSM Parameter Store by the ECS agent at task start.
      secrets = [
        { name = "DJANGO_SECRET_KEY", valueFrom = aws_ssm_parameter.django_secret_key.arn },
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name            = local.name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # Give the app a moment to boot before the ALB starts failing health checks.
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true # required for ECR/CloudWatch reachability without NAT
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.name
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.http]
}
