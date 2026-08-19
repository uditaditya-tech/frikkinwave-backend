# ---------------------------------------------------------------------------
# The cluster itself.
#
# Cost note: the control plane bills $0.10/hr ($73/mo) from creation to deletion,
# regardless of load. That is more per hour than the entire previous ECS stack —
# which is why eks-down.sh exists and why the budget alarm is not optional.
# ---------------------------------------------------------------------------

resource "aws_eks_cluster" "main" {
  name     = local.name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    # NOTE: EKS freezes the cluster's AZ set at creation. Adding a subnet in a
    # NEW availability zone to an existing cluster is rejected outright:
    #
    #   InvalidParameterException: Provided subnets belong to the AZs
    #   'ap-south-1c,ap-south-1b,ap-south-1a'. But they should belong to the
    #   exact set of AZs 'ap-south-1b,ap-south-1a' in which subnets were
    #   provided during cluster creation.
    #
    # Changing the control plane's AZs therefore means recreating the cluster.
    # A fresh apply picks up all three subnets; the ignore_changes below stops
    # Terraform from attempting an update that the API cannot ever satisfy.
    #
    # Node placement does not depend on this — the node group below spans all
    # three subnets regardless, because only control-plane ENIs live here.
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = true # kubectl from a laptop
    endpoint_private_access = true # nodes reach the API without leaving the VPC
  }

  access_config {
    # API mode replaces the legacy aws-auth ConfigMap with real AWS resources.
    # Access is granted below via aws_eks_access_entry.
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # Send control-plane logs to CloudWatch. `authenticator` is the one that tells
  # you *why* a principal was denied — worth having before you need it.
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  depends_on = [aws_iam_role_policy_attachment.cluster]
  tags       = local.tags

  lifecycle {
    # See the note in vpc_config: the AZ set is immutable after creation, so a
    # diff here can never converge. Recreate the cluster to change it.
    ignore_changes = [vpc_config[0].subnet_ids]
  }
}

# ---------------------------------------------------------------------------
# Managed node group.
#
# Managed (not self-managed, not Fargate profiles): AWS handles AMI patching and
# rolling upgrades, and unlike Fargate-on-EKS it can run DaemonSets — which the
# observability stack in a later phase requires.
# ---------------------------------------------------------------------------

resource "aws_eks_node_group" "main" {
  cluster_name = aws_eks_cluster.main.name
  # A PREFIX, not a fixed name. subnet_ids and several other attributes force
  # replacement when changed, and a fixed name makes create_before_destroy
  # impossible (the new group would collide with the old one). With a prefix,
  # Terraform can stand the replacement up before tearing the old one down.
  node_group_name_prefix = "${local.name}-ng-"
  node_role_arn          = aws_iam_role.node.arn
  subnet_ids             = aws_subnet.public[*].id

  instance_types = var.node_instance_types
  ami_type       = "AL2023_ARM_64_STANDARD" # must match the ARM instance type
  capacity_type  = "ON_DEMAND"
  disk_size      = 20

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  depends_on = [aws_iam_role_policy_attachment.node]
  tags       = local.tags

  lifecycle {
    # Replacing a node group means every pod on it is evicted. Without this,
    # Terraform destroys the old group first and the cluster has nowhere to run
    # anything until the new nodes register — a full outage. Create first, let
    # pods reschedule, then remove the old.
    create_before_destroy = true

    # The cluster autoscaler / HPA may move this; don't fight it on every apply.
    ignore_changes = [scaling_config[0].desired_size]
  }
}

# ---------------------------------------------------------------------------
# Managed addons — the components every cluster needs, kept patched by AWS.
# Installed after the node group so their pods have somewhere to schedule.
# ---------------------------------------------------------------------------

locals {
  # CoreDNS ships with *preferred* anti-affinity, so both replicas can land on
  # one node — and did. Losing that node then takes out all cluster DNS at once,
  # which turns a survivable node failure into a total outage: surviving pods
  # stay up but can no longer resolve the database or each other.
  #
  # requiredDuringScheduling makes the spread a hard constraint. Safe here
  # because the node group always runs >= 2 nodes; with a single node the second
  # replica would sit Pending by design.
  addon_config = {
    coredns = jsonencode({
      replicaCount = 2
      affinity = {
        podAntiAffinity = {
          requiredDuringSchedulingIgnoredDuringExecution = [{
            topologyKey = "kubernetes.io/hostname"
            labelSelector = {
              matchExpressions = [{
                key      = "k8s-app"
                operator = "In"
                values   = ["kube-dns"]
              }]
            }
          }]
        }
      }
    })
  }
}

resource "aws_eks_addon" "this" {
  for_each = toset(["vpc-cni", "coredns", "kube-proxy"])

  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  configuration_values        = lookup(local.addon_config, each.value, null)

  depends_on = [aws_eks_node_group.main]
  tags       = local.tags
}

# ---------------------------------------------------------------------------
# Console access.
#
# Without an access entry, the AWS console shows the cluster but reports
# "your current user or role does not have access to Kubernetes objects" and
# lists no pods or deployments. The creator is admin automatically; this adds an
# explicit second principal for when you browse as a different identity.
# ---------------------------------------------------------------------------

resource "aws_eks_access_entry" "console_admin" {
  count         = var.console_admin_principal_arn == "" ? 0 : 1
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.console_admin_principal_arn
  type          = "STANDARD"
  tags          = local.tags
}

resource "aws_eks_access_policy_association" "console_admin" {
  count         = var.console_admin_principal_arn == "" ? 0 : 1
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.console_admin_principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.console_admin]
}
