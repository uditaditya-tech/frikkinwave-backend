# Runtime secrets live in SSM Parameter Store (SecureString, free tier).
# The ECS task definition pulls these via its `secrets` block (valueFrom ARN),
# so the values never appear in the task definition or `terraform plan` output
# in plaintext at the container-config layer.

locals {
  database_url = "postgres://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
}

resource "aws_ssm_parameter" "django_secret_key" {
  name  = "/${local.name}/DJANGO_SECRET_KEY"
  type  = "SecureString"
  value = var.django_secret_key

  tags = { Name = "${local.name}-django-secret-key" }
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/${local.name}/DATABASE_URL"
  type  = "SecureString"
  value = local.database_url

  tags = { Name = "${local.name}-database-url" }
}
