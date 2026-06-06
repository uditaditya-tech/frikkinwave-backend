# Terraform-generated master password. special=false keeps it URL-safe so it
# drops straight into a postgres:// DATABASE_URL without percent-encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

# Stable suffix for the final-snapshot name, so destroy → recreate → destroy
# cycles don't collide on a duplicate snapshot identifier. Stored in state, so
# it doesn't churn the plan (unlike timestamp()).
resource "random_id" "final_snapshot" {
  byte_length = 4
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

  # Restore from a snapshot when db_snapshot_identifier is set (rehydrate a prior
  # destroy's data). Only consulted at create time — RDS restores schema + data +
  # the pgvector extension from the snapshot, then Terraform modifies the master
  # password to random_password.db so it stays in sync with DATABASE_URL in SSM.
  # Empty (default) → fresh empty DB, unchanged behavior.
  snapshot_identifier = var.db_snapshot_identifier != "" ? var.db_snapshot_identifier : null

  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false
  multi_az                = false
  backup_retention_period = var.db_backup_retention_days

  # Data retention on destroy: by default `terraform destroy` takes a final
  # snapshot (named below) so the data survives and can rehydrate a future
  # deploy via `snapshot_identifier`. Set db_skip_final_snapshot=true to opt out
  # (wipe cleanly) when you don't need the data.
  skip_final_snapshot       = var.db_skip_final_snapshot
  final_snapshot_identifier = var.db_skip_final_snapshot ? null : "${local.name}-final-${random_id.final_snapshot.hex}"
  deletion_protection       = var.db_deletion_protection
  apply_immediately         = true

  tags = { Name = "${local.name}-db" }
}
