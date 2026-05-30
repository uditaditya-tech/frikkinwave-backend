#!/usr/bin/env bash
#
# Run database migrations (+ seed reference data) as a one-off Fargate task,
# reusing the app's task definition with a command override.
#
# Why a separate task: migrations must NOT run on web-container start (that would
# race across concurrent tasks). This runs exactly once, on demand.
#
# Usage:  ./infra/scripts/run-migrations.sh
# Requires: terraform state present (after `terraform apply`), AWS creds.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"
tf() { terraform -chdir="${TF_DIR}" output -raw "$1"; }

CLUSTER="$(tf ecs_cluster)"
FAMILY="$(tf ecs_task_family)"
CONTAINER="$(tf ecs_container_name)"
SUBNETS="$(tf public_subnet_ids)"
SG="$(tf tasks_security_group_id)"
REGION="$(tf aws_region)"

echo "==> Launching one-off migrate + seed task on cluster ${CLUSTER}"
TASK_ARN="$(aws ecs run-task \
  --cluster "${CLUSTER}" \
  --launch-type FARGATE \
  --task-definition "${FAMILY}" \
  --region "${REGION}" \
  --count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SG}],assignPublicIp=ENABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"${CONTAINER}\",\"command\":[\"sh\",\"-c\",\"python manage.py migrate --noinput && python manage.py seed_music_data\"]}]}" \
  --query 'tasks[0].taskArn' --output text)"

echo "    task: ${TASK_ARN}"
echo "==> Waiting for it to finish..."
aws ecs wait tasks-stopped --cluster "${CLUSTER}" --tasks "${TASK_ARN}" --region "${REGION}"

EXIT_CODE="$(aws ecs describe-tasks --cluster "${CLUSTER}" --tasks "${TASK_ARN}" --region "${REGION}" \
  --query 'tasks[0].containers[0].exitCode' --output text)"

echo "==> Task exited with code ${EXIT_CODE}"
if [ "${EXIT_CODE}" != "0" ]; then
  echo "    Migrations FAILED. Check logs:"
  echo "    aws logs tail /ecs/${CLUSTER} --region ${REGION} --since 10m"
  exit 1
fi
echo "==> Migrations + seed complete."
