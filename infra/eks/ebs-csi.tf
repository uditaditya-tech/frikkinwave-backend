# ---------------------------------------------------------------------------
# EBS CSI driver — persistent storage.
#
# Until this exists the cluster cannot provision a volume AT ALL. That is not
# obvious from the outside: `kubectl get storageclass` shows a `gp2` class and
# everything looks fine. Two separate things are wrong with it.
#
#   1. It is not marked default (`IsDefaultClass: No`). A PVC that does not name
#      a class therefore gets no class, and the binder reports
#      "no persistent volumes available for this claim and no storage class is
#      set" — it never reaches a provisioner at all.
#
#   2. Behind that, its provisioner is `kubernetes.io/aws-ebs` — the *in-tree*
#      one, removed from Kubernetes long before 1.36. So naming it explicitly
#      does not help either; the claim just hangs Pending with nothing watching.
#
# The gp3 class below fixes both at once: a real CSI provisioner, marked
# default. gp2 is left in place — it is inert, and deleting a StorageClass that
# EKS ships is a fight with the addon, not a cleanup.
#
# Verify with a PVC that has a CONSUMER POD. Both classes are
# WaitForFirstConsumer (they must be — EBS volumes are zonal and have to be
# created in whichever AZ the pod lands in), so a pod-less PVC sits Pending even
# when everything is working correctly. A bare PVC is not a test.
# ---------------------------------------------------------------------------

locals {
  ebs_csi_sa = "ebs-csi-controller-sa"
  ebs_csi_ns = "kube-system"
}

data "aws_iam_policy_document" "ebs_csi_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    # Same reasoning as lb-controller.tf: without the :sub condition any pod in
    # the cluster could assume a role that can create and attach EBS volumes.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${local.ebs_csi_ns}:${local.ebs_csi_sa}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${local.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume.json
  tags               = local.tags
}

# Unlike the load balancer controller, AWS publishes and maintains a managed
# policy for this one, so there is nothing to vendor.
resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name = aws_eks_cluster.main.name
  addon_name   = "aws-ebs-csi-driver"

  # The IRSA link. Without it the controller falls back to the node role, which
  # has no EBS permissions, and every provision fails with AccessDenied buried
  # in the csi-provisioner sidecar's logs.
  service_account_role_arn = aws_iam_role.ebs_csi.arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  depends_on = [
    aws_eks_node_group.main,
    aws_iam_role_policy_attachment.ebs_csi,
  ]

  tags = local.tags
}

# ---------------------------------------------------------------------------
# The default StorageClass.
#
# gp3 rather than gp2: cheaper per GB, and its 3000 IOPS / 125 MB/s baseline is
# decoupled from volume size. On gp2 throughput scales with capacity, so a small
# Kafka volume would be throttled purely for being small.
# ---------------------------------------------------------------------------
resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3"

    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }

  storage_provisioner = "ebs.csi.aws.com"
  reclaim_policy      = "Delete"

  # Zonal volumes: binding must wait until the scheduler has picked a node, or
  # the volume gets created in an AZ the pod cannot reach.
  volume_binding_mode = "WaitForFirstConsumer"

  # A Kafka broker that fills its disk is a broker that stays down. Expansion in
  # place is the only remedy that does not involve losing the replica.
  allow_volume_expansion = true

  parameters = {
    type      = "gp3"
    encrypted = "true"
  }

  depends_on = [aws_eks_addon.ebs_csi]
}
