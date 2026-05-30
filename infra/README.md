# Infrastructure (Terraform)

AWS infra for frikkinwave-backend, provisioned with Terraform.
Target: ECS Fargate behind an Application Load Balancer (Phase 1, sub-steps 1.9–1.11).

```
infra/
├── terraform/        # All Terraform config
│   ├── main.tf            # provider, version pins, local state
│   ├── variables.tf       # inputs (region, image_tag, secrets, db, …)
│   ├── network.tf         # VPC, public + private subnets, IGW, security groups
│   ├── ecr.tf             # container registry + lifecycle policy
│   ├── rds.tf             # Postgres 16 instance + subnet group + password
│   ├── secrets.tf         # SSM Parameter Store: DJANGO_SECRET_KEY, DATABASE_URL
│   ├── iam.tf             # ECS execution + task roles (+ SSM read policy)
│   ├── logs.tf            # CloudWatch log group
│   ├── alb.tf             # load balancer, target group, HTTP listener
│   ├── ecs.tf             # cluster, task definition (secrets), service
│   ├── outputs.tf         # ALB DNS, ECR URL, RDS endpoint, health URL
│   └── terraform.tfvars.example
└── scripts/
    ├── push-image.sh      # build (linux/arm64) + push to ECR
    └── run-migrations.sh  # one-off Fargate task: migrate + seed
```

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity` works)
- Terraform >= 1.6
- Docker running (for the image build/push)

## First deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in django_secret_key
terraform init

# 1) Create ECR first so there's somewhere to push the image:
terraform apply -target=aws_ecr_repository.app

# 2) Build + push the image:
../scripts/push-image.sh            # tags :latest

# 3) Create everything else (RDS, ALB, ECS, networking, secrets, …):
terraform apply

# 4) Run migrations + seed reference data (one-off Fargate task):
../scripts/run-migrations.sh

# 5) Verify the service is live behind the ALB:
curl "$(terraform output -raw health_check_url)"   # → {"status": "ok"}
```

> RDS takes ~5–10 min to provision on the first `terraform apply`.

## Updating the running app

```bash
../scripts/push-image.sh v2
terraform apply -var image_tag=v2
# If the deploy includes new migrations:
../scripts/run-migrations.sh
```

## Tear down (back to $0)

```bash
terraform destroy
```

## Notes / scope

- **State is local** (`terraform.tfstate`, git-ignored). Migrate to an S3 backend +
  DynamoDB lock once the account is bootstrapped — see the commented block in `main.tf`.
- **No NAT gateway.** Tasks run in public subnets with public IPs (saves ~$32/mo);
  the task security group still only accepts traffic from the ALB.
- **HTTP only** in 1.9. HTTPS (443 + ACM cert) and `api.frikkinwave.com` land in 1.11.
- **Database:** RDS Postgres 16 (`db.t4g.micro`) in private subnets, not internet-reachable;
  the tasks SG is the only thing allowed to reach it on 5432. Ephemeral-dev posture —
  `terraform destroy` drops the DB; `run-migrations.sh` rebuilds schema + seed on the next apply.
- **Secrets:** `DJANGO_SECRET_KEY` and `DATABASE_URL` live in SSM Parameter Store
  (SecureString) and are injected into the container via the task definition's `secrets`
  block. The Terraform-generated DB password never appears in the task definition.
- **ALB health checks + ALLOWED_HOSTS:** ALB health checks reach the container with the
  task's private IP as the Host header. `config/settings/production.py` appends that IP
  (from the ECS metadata endpoint) to `ALLOWED_HOSTS` so `/api/health/` returns 200.
- **ARM64/Graviton** tasks — the push script builds `linux/arm64` to match.

## Rough cost while running

In `ap-south-1` (Mumbai): ALB ~$18/mo + 1× Fargate (0.25 vCPU/0.5 GB) ~$10/mo +
RDS `db.t4g.micro` ~$13/mo + 20 GB gp3 ~$2/mo + minimal ECR/logs ≈ **~$43/mo**.
`terraform destroy` removes all of it.
