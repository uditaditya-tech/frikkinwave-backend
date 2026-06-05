data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: used by the ECS agent to pull the image from ECR and ship
# container logs to CloudWatch.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Task role: assumed by the application itself. Empty for now — gains S3
# permissions in later sub-steps.
resource "aws_iam_role" "task" {
  name               = "${local.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# Let the execution role read the SSM SecureString params (and decrypt them)
# so the ECS agent can inject them into the container as `secrets`.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadAppSecrets"
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.django_secret_key.arn,
      aws_ssm_parameter.database_url.arn,
      aws_ssm_parameter.openai_api_key.arn,
    ]
  }

  statement {
    sid       = "DecryptSecrets"
    actions   = ["kms:Decrypt"]
    resources = ["*"] # AWS-managed alias/aws/ssm key; scope down if a CMK is introduced
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${local.name}-execution-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}
