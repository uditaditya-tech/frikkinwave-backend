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
│   ├── alb.tf             # load balancer, target group, HTTP→HTTPS redirect, HTTPS listener
│   ├── dns.tf             # Route 53 zone (api subdomain), ACM cert, alias record
│   ├── ecs.tf             # cluster, task definition (secrets), service
│   ├── outputs.tf         # ALB DNS, ECR URL, RDS endpoint, nameservers, URLs
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

# 3) Create the Route 53 hosted zone, then delegate the subdomain:
terraform apply -target=aws_route53_zone.api
terraform output api_nameservers
#   → add these 4 as NS records for `api` at the parent domain's DNS (e.g. GoDaddy).
#   Verify: dig +short NS api.frikkinwave.com @8.8.8.8

# 4) Create everything else (RDS, ALB, ECS, ACM cert, HTTPS, alias, secrets, …):
terraform apply
#   The ACM validation step blocks until the cert is issued — needs step 3 done.

# 5) Run migrations + seed reference data (one-off Fargate task):
../scripts/run-migrations.sh

# 6) Verify the API is live over HTTPS:
curl "$(terraform output -raw api_url)"            # → {"status": "ok"}
```

> RDS takes ~5–10 min to provision; ACM validation a few minutes after delegation.

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
- **DNS + HTTPS:** `api.frikkinwave.com` is a Route 53 hosted zone delegated from the
  parent domain (apex stays with the registrar/Vercel). ACM issues the TLS cert
  (DNS-validated); the ALB has an HTTPS:443 listener and redirects HTTP:80 → 443.
  Django sets `SECURE_PROXY_SSL_HEADER` to trust the ALB's `X-Forwarded-Proto`.
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
RDS `db.t4g.micro` ~$13/mo + 20 GB gp3 ~$2/mo + Route 53 zone $0.50/mo +
minimal ECR/logs ≈ **~$44/mo**. `terraform destroy` removes all of it.

> Note: `terraform destroy` deletes the Route 53 hosted zone, which changes its
> nameservers. After a later `apply` you must re-point the `api` NS records at the
> registrar to the new nameservers (`terraform output api_nameservers`).
