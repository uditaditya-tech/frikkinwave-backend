# ---------------------------------------------------------------------------
# AWS Load Balancer Controller — the bridge between Kubernetes and ALBs.
#
# It watches Ingress objects and provisions a real ALB, listeners, and target
# groups to match. This is the piece that lets the app be reached from the
# internet at all; without it an Ingress is an inert object and nothing happens.
#
# It is also the first real use of IRSA: the controller pod needs broad EC2 and
# ELB permissions, and granting those to the *node* role would hand them to
# every pod on the cluster. Instead its ServiceAccount token is exchanged for a
# scoped IAM role, so the permission lives with the workload that needs it.
# ---------------------------------------------------------------------------

locals {
  # OIDC issuer without the scheme — IAM condition keys are written this way.
  oidc_host        = replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")
  lb_controller_sa = "aws-load-balancer-controller"
  lb_controller_ns = "kube-system"
}

# The permission set published by the controller project, vendored into the repo
# rather than fetched at apply time: an apply must not depend on GitHub being up,
# and a silent upstream change to a policy this broad should show as a git diff.
resource "aws_iam_policy" "lb_controller" {
  name        = "${local.name}-lb-controller"
  description = "AWS Load Balancer Controller — from the upstream project's published policy."
  policy      = file("${path.module}/policies/aws-lb-controller.json")
  tags        = local.tags
}

data "aws_iam_policy_document" "lb_controller_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    # Bind the role to exactly one ServiceAccount in one namespace. Without the
    # :sub condition any pod in the cluster could assume it, which would defeat
    # the point of using IRSA over the node role.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${local.lb_controller_ns}:${local.lb_controller_sa}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lb_controller" {
  name               = "${local.name}-lb-controller"
  assume_role_policy = data.aws_iam_policy_document.lb_controller_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "lb_controller" {
  role       = aws_iam_role.lb_controller.name
  policy_arn = aws_iam_policy.lb_controller.arn
}

# ---------------------------------------------------------------------------
# The controller itself.
#
# Installed by Terraform rather than by the app chart because it is cluster
# infrastructure with a lifecycle tied to the cluster, not to an app release —
# and because the app's Ingress cannot reconcile until this exists.
# ---------------------------------------------------------------------------
resource "helm_release" "lb_controller" {
  name       = local.lb_controller_sa
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.lb_controller_chart_version
  namespace  = local.lb_controller_ns

  # The webhook must be serving before an Ingress can be admitted; failing the
  # apply here is better than a green apply and a silently unreachable app.
  wait    = true
  timeout = 600

  set = [
    { name = "clusterName", value = aws_eks_cluster.main.name },
    { name = "region", value = var.region },
    { name = "vpcId", value = aws_vpc.main.id },
    { name = "serviceAccount.create", value = "true" },
    { name = "serviceAccount.name", value = local.lb_controller_sa },
    # The IRSA link: this annotation is what makes the pod's projected token
    # exchangeable for the role above.
    {
      name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
      value = aws_iam_role.lb_controller.arn
    },
    # One replica: the nodes are t4g.small (2 GB) and the default of two buys
    # controller HA that this stack does not need — it is torn down nightly.
    { name = "replicaCount", value = "1" },
  ]

  depends_on = [
    aws_eks_node_group.main,
    aws_eks_addon.this,
    aws_iam_role_policy_attachment.lb_controller,
  ]
}
