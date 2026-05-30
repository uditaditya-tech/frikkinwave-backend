# Infrastructure (Terraform)

AWS infra for frikkinwave-backend, provisioned with Terraform.
Target: ECS Fargate behind an Application Load Balancer (Phase 1, sub-steps 1.9–1.11).

```
infra/
├── terraform/        # All Terraform config
│   ├── main.tf            # provider, version pins, local state
│   ├── variables.tf       # inputs (region, image_tag, secrets, …)
│   ├── network.tf         # VPC, public subnets, IGW, security groups
│   ├── ecr.tf             # container registry + lifecycle policy
│   ├── iam.tf             # ECS execution + task roles
│   ├── logs.tf            # CloudWatch log group
│   ├── alb.tf             # load balancer, target group, HTTP listener
│   ├── ecs.tf             # cluster, task definition, service
│   ├── outputs.tf         # ALB DNS, ECR URL, health URL
│   └── terraform.tfvars.example
└── scripts/
    └── push-image.sh      # build (linux/arm64) + push to ECR
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

# 3) Create everything else (ALB, ECS, networking, …):
terraform apply

# 4) Verify the service is live behind the ALB:
curl "$(terraform output -raw health_check_url)"   # → {"status": "ok"}
```

## Updating the running app

```bash
../scripts/push-image.sh v2
terraform apply -var image_tag=v2
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
- **No database yet.** `database_url` is a placeholder until RDS in 1.10 — the
  `/api/health/` check doesn't touch the DB, so the service is healthy without it.
- **Secrets are plain env vars** in the task definition for now; they move to
  SSM/Secrets Manager in 1.10.
- **ARM64/Graviton** tasks — the push script builds `linux/arm64` to match.

## Rough cost while running

In `ap-south-1` (Mumbai): ALB ~$18/mo + 1× Fargate (0.25 vCPU/0.5 GB) ~$10/mo +
minimal ECR/logs. `terraform destroy` removes all of it.
