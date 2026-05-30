#!/usr/bin/env bash
#
# Build the app image and push it to the ECR repo created by Terraform.
#
# Usage:  ./infra/scripts/push-image.sh [tag]
#         tag defaults to "latest".
#
# Requires: terraform state present (run `terraform apply` for ECR first),
# docker running, and AWS credentials configured.

set -euo pipefail

TAG="${1:-latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"
REPO_ROOT="${SCRIPT_DIR}/../.."

REPO_URL="$(terraform -chdir="${TF_DIR}" output -raw ecr_repository_url)"
REGION="$(terraform -chdir="${TF_DIR}" output -raw aws_region)"
REGISTRY="${REPO_URL%/*}" # strip the trailing /<repo-name>

echo "==> Logging in to ECR registry ${REGISTRY}"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

echo "==> Building image (linux/arm64) ${REPO_URL}:${TAG}"
docker build --platform linux/arm64 -t "${REPO_URL}:${TAG}" "${REPO_ROOT}"

echo "==> Pushing ${REPO_URL}:${TAG}"
docker push "${REPO_URL}:${TAG}"

CLUSTER="$(basename "${REPO_URL}")" # cluster + service share the project-env name
echo "==> Done. To roll the running service onto this image:"
echo "    aws ecs update-service --cluster ${CLUSTER} --service ${CLUSTER} --force-new-deployment --region ${REGION}"
