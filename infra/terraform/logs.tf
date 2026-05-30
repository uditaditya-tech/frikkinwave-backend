resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 14

  tags = { Name = "${local.name}-logs" }
}
