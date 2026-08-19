# ---------------------------------------------------------------------------
# Container registry.
#
# The old repo died with the ECS stack (force_delete), so this recreates it.
# Images are linux/arm64 to match the Graviton node group — an amd64 image
# schedules fine and then CrashLoopBackOffs with "exec format error", which
# reads like an app bug rather than an architecture mismatch.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name = local.name

  # Let `terraform destroy` remove the repo even when it holds images. The
  # image is rebuilt from source on the next push; nothing here is precious.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, { Name = "${local.name}-ecr" })
}

# Storage is billed per GB — keep only what a rollback could need.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        # Counts ENTRIES, not builds. Kept honest by the --provenance=false
        # flag in app-deploy.sh: with attestations on, one build is three
        # entries and this quietly becomes a 3-deploy rollback window.
        description = "Keep only the last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      }
    ]
  })
}
