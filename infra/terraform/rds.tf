# Terraform-generated master password. special=false keeps it URL-safe so it
# drops straight into a postgres:// DATABASE_URL without percent-encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db-subnet"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${local.name}-db-subnet" }
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false
  multi_az                = false
  backup_retention_period = var.db_backup_retention_days

  # Ephemeral-dev posture: destroy wipes the DB cleanly. Schema + seed data are
  # rebuilt by scripts/run-migrations.sh on the next apply.
  skip_final_snapshot = var.db_skip_final_snapshot
  deletion_protection = var.db_deletion_protection
  apply_immediately   = true

  tags = { Name = "${local.name}-db" }
}
