# ---------------------------------------------------------------------------
# Postgres — restored from a snapshot, not created empty.
#
# The snapshot carries the pgvector extension, the reference data, the real
# profiles and the whole demo-* Phase 5 dataset, so every list endpoint has
# enough rows to paginate against a frontend. A fresh DB would need migrate +
# seed + re-embedding (which costs OpenAI calls) to get back to the same place.
#
# It lives in the public subnets because this VPC has no private ones (a NAT
# gateway is ~$32/mo and the whole point of this stack is being cheap). It is
# NOT internet-reachable: publicly_accessible=false means no public IP is
# assigned, and the security group below only admits the cluster.
# ---------------------------------------------------------------------------

# Master password. special=false keeps it URL-safe so it drops into a
# postgres:// DATABASE_URL without percent-encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

# Stable suffix for the final-snapshot name, so destroy → recreate → destroy
# cycles don't collide on a duplicate identifier. Lives in state, so it doesn't
# churn the plan the way timestamp() would.
resource "random_id" "final_snapshot" {
  byte_length = 4
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db-subnet"
  subnet_ids = aws_subnet.public[*].id

  tags = merge(local.tags, { Name = "${local.name}-db-subnet" })
}

# ---------------------------------------------------------------------------
# Only the cluster may reach Postgres.
#
# The source is the EKS-managed *cluster* security group, which EKS attaches to
# every managed node group instance. With the VPC CNI, pod traffic leaves
# through the node's ENI and therefore carries this SG — so allowing it here
# admits the web pods, the worker, and the migration Job, and nothing else.
# ---------------------------------------------------------------------------
resource "aws_security_group" "rds" {
  name        = "${local.name}-rds-sg"
  description = "Postgres: accept traffic only from the EKS cluster."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from cluster workloads only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.main.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name}-rds-sg" })
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"

  # On a snapshot restore RDS keeps the database name baked into the snapshot,
  # and passing db_name forces replacement on every plan. Only set it when
  # creating an empty instance.
  db_name = var.db_snapshot_identifier != "" ? null : var.db_name

  username = var.db_username

  # The snapshot's master password is whatever the destroyed stack generated —
  # unknowable now. Setting password here makes RDS modify it after the restore
  # completes, so it matches the DATABASE_URL we hand to the pods.
  password = random_password.db.result

  # Consulted only at create time. Empty => fresh empty DB.
  snapshot_identifier = var.db_snapshot_identifier != "" ? var.db_snapshot_identifier : null

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period = var.db_backup_retention_days

  # Data survives teardown: destroy takes a final snapshot that a later apply
  # can restore from via db_snapshot_identifier. Snapshot storage up to the
  # instance size is free, so this costs nothing while the stack is down.
  skip_final_snapshot       = var.db_skip_final_snapshot
  final_snapshot_identifier = var.db_skip_final_snapshot ? null : "${local.name}-final-${random_id.final_snapshot.hex}"
  deletion_protection       = var.db_deletion_protection
  apply_immediately         = true

  tags = merge(local.tags, { Name = "${local.name}-db" })
}
