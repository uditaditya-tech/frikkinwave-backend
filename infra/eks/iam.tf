# ---------------------------------------------------------------------------
# IAM for the control plane and the nodes.
#
# Two distinct roles, deliberately:
#   - the CLUSTER role lets the EKS control plane manage AWS resources on your
#     behalf (ENIs, load balancers).
#   - the NODE role is what EC2 instances assume; it is broad, which is exactly
#     why workloads should use IRSA (per-pod roles) instead of inheriting it.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.name}-eks-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-eks-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",               # pod networking (VPC CNI)
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly", # pull from ECR
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

# ---------------------------------------------------------------------------
# OIDC provider — the foundation of IRSA.
#
# It lets a Kubernetes ServiceAccount token be exchanged for AWS credentials, so
# a *pod* can hold an IAM role instead of every pod inheriting the node's role.
# Phase 3 (External Secrets, LB controller) builds directly on this.
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "cluster" {
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]
  tags            = local.tags
}
