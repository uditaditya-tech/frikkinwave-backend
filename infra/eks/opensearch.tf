# ---------------------------------------------------------------------------
# OpenSearch — the search index.
#
# Managed rather than self-hosted, for the same reason RDS is: this stack
# already carries two stateful operators (Strimzi, the Prometheus Operator) and
# both have cost real debugging time around PVC ownership and teardown ordering.
# A third one to run a search cluster is not where the interesting problems are.
#
# **This domain holds no source of truth.** Every document in it is derived from
# Postgres and rebuilt by `reindex_profiles`, which is why it takes no snapshot
# on teardown and needs no restore on apply — unlike RDS, which does both. The
# consequence is the thing to remember: a rebuilt stack comes up with a HEALTHY,
# EMPTY cluster, and search returns nothing while every probe stays green. The
# rebuild in app-deploy.sh is what closes that window, not this file.
#
# Cost: roughly $0.04/hr while the stack is up (t3.small.search + 10GB gp3), on
# top of the ~$0.26/hr the rest of it runs at.
# ---------------------------------------------------------------------------

# Master password for fine-grained access control.
#
# Two constraints pull in opposite directions here, and both have to hold:
# AWS requires an uppercase, a lowercase, a number AND a special character, so
# `special = false` (which is what rds.tf uses) is rejected outright. But the
# value also drops into an https://user:pass@host URL, so a `/` or `@` in it
# would corrupt the URL rather than fail loudly. `override_special` narrows the
# alphabet to characters that satisfy AWS and are URL-safe unencoded.
resource "random_password" "opensearch" {
  length           = 32
  special          = true
  override_special = "-_"
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
}

# ---------------------------------------------------------------------------
# Only the cluster may reach the domain.
#
# Same source as the RDS security group: the EKS-managed *cluster* security
# group, which is attached to every managed node group instance. With the VPC
# CNI, pod traffic leaves through the node's ENI and carries this SG, so this
# admits the web pods, the consumers and the rebuild Job, and nothing else.
#
# A VPC domain has no internet-facing endpoint at all, so this is the second
# layer rather than the only one.
# ---------------------------------------------------------------------------
resource "aws_security_group" "opensearch" {
  name        = "${local.name}-opensearch-sg"
  description = "OpenSearch: accept traffic only from the EKS cluster."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTPS from cluster workloads only"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.main.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name}-opensearch-sg" })
}

resource "aws_opensearch_domain" "main" {
  domain_name    = "${local.name}-search"
  engine_version = var.opensearch_engine_version

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = 1

    # One node, so no zone awareness — and therefore exactly ONE subnet below.
    # Passing more than one subnet without zone awareness is rejected.
    #
    # The index is derived and rebuildable in minutes, so a second node would
    # buy availability, not durability. Worth revisiting when search being down
    # for the length of a rebuild actually costs something.
    zone_awareness_enabled = false
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.opensearch_volume_size
  }

  vpc_options {
    subnet_ids         = [aws_subnet.public[0].id]
    security_group_ids = [aws_security_group.opensearch.id]
  }

  # ---------------------------------------------------------------------------
  # Fine-grained access control, with the internal user database.
  #
  # The app authenticates with a username and password embedded in
  # OPENSEARCH_URL, exactly as it does with Postgres — no IAM signing, so no
  # boto3 and no SigV4 signer in the request path.
  #
  # The three blocks below are not optional extras: FGAC REQUIRES encryption at
  # rest, node-to-node encryption and enforced HTTPS. Omit any one and the
  # create fails with a validation error rather than a helpful message.
  # ---------------------------------------------------------------------------
  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true

    master_user_options {
      master_user_name     = var.opensearch_username
      master_user_password = random_password.opensearch.result
    }
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  # Open at the resource-policy layer, closed at every other one. This is the
  # documented pairing for a VPC domain with FGAC: the domain is unreachable
  # from outside the VPC and the security group admits only the cluster, so
  # authorization is FGAC's job. A restrictive policy here in ADDITION to FGAC
  # is the well-known way to lock yourself out of your own domain.
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "*" }
      Action    = "es:*"
      Resource  = "arn:aws:es:${var.region}:${data.aws_caller_identity.current.account_id}:domain/${local.name}-search/*"
    }]
  })

  tags = merge(local.tags, { Name = "${local.name}-search" })
}
