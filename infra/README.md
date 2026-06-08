# Infrastructure (Terraform)

AWS infra for frikkinwave-backend, split into two Terraform stacks so the
app can be torn down and rebuilt freely without disturbing DNS or the TLS cert.

```
infra/
├── dns/              # PERSISTENT stack — create once, never `terraform destroy`
│   ├── main.tf            # provider, version pins, local state
│   ├── variables.tf       # region, project, environment, api_domain
│   ├── dns.tf             # Route 53 zone (api subdomain), ACM cert + validation
│   └── outputs.tf         # nameservers, zone_id, certificate_arn
│
├── terraform/        # APP stack — the disposable destroy/apply layer
│   ├── main.tf            # provider, version pins, local state
│   ├── variables.tf       # inputs (region, image_tag, secrets, db, api_domain, …)
│   ├── network.tf         # VPC, public + private subnets, IGW, security groups
│   ├── ecr.tf             # container registry + lifecycle policy
│   ├── rds.tf             # Postgres 16 instance + subnet group + password
│   ├── secrets.tf         # SSM Parameter Store: DJANGO_SECRET_KEY, DATABASE_URL
│   ├── iam.tf             # ECS execution + task roles (+ SSM read policy)
│   ├── logs.tf            # CloudWatch log group
│   ├── alb.tf             # load balancer, target group, HTTP→HTTPS redirect, HTTPS listener
│   ├── dns.tf             # data lookups (zone + cert) + ALB alias record
│   ├── ecs.tf             # cluster, task definition (secrets), service
│   ├── outputs.tf         # ALB DNS, ECR URL, RDS endpoint, URLs
│   └── terraform.tfvars.example
└── scripts/
    ├── push-image.sh      # build (linux/arm64) + push to ECR
    ├── run-migrations.sh  # one-off Fargate task: migrate + seed
    ├── teardown.sh        # destroy app stack (final snapshot by default; --wipe to skip)
    └── bring-up.sh        # rebuild + auto-restore latest snapshot (ECR→push→apply→migrate→verify)
```

**Why two stacks:** the Route 53 zone's nameservers are delegated from the
registrar (GoDaddy) once, and the ACM cert is tied to that zone. If those lived
in the app stack, every `terraform destroy` would rotate the nameservers and
force a re-delegation. Keeping them in a separate persistent stack means the app
stack discovers them via `data` sources and can be destroyed/recreated at will.

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity` works)
- Terraform >= 1.6
- Docker running (for the image build/push)

## One-time DNS bootstrap (persistent stack)

Do this **once**. Don't destroy it afterwards.

```bash
cd infra/dns
terraform init
terraform apply
terraform output api_nameservers
#   → add these 4 as NS records for `api` at the parent domain's DNS (GoDaddy).
#   Verify: dig +short NS api.frikkinwave.com @8.8.8.8
#   The ACM cert validates automatically a few minutes after delegation.
```

## App deploy (disposable stack)

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in django_secret_key
terraform init

# 1) Create ECR first so there's somewhere to push the image:
terraform apply -target=aws_ecr_repository.app

# 2) Build + push the image:
../scripts/push-image.sh            # tags :latest

# 3) Create everything else (RDS, ALB, ECS, HTTPS listener, alias, secrets, …):
terraform apply
#   Reads the zone + cert from the dns stack via data sources.

# 4) Run migrations + seed reference data (one-off Fargate task):
../scripts/run-migrations.sh

# 5) Verify the API is live over HTTPS:
curl "$(terraform output -raw api_url)"            # → {"status": "ok"}
```

> RDS takes ~5–10 min to provision on the first `terraform apply`.

## Updating the running app

```bash
../scripts/push-image.sh v2
terraform apply -var image_tag=v2
# If the deploy includes new migrations:
../scripts/run-migrations.sh
```

## Phase 2: Redis broker + Celery worker (2.9 — DEPLOYED)

Phase 2 added async work (Celery) and the AI features. This infra is **applied
and live in prod** (re-deployed 2026-06-05; verified end-to-end — a profile save
ran through the worker → OpenAI embedding → pgvector → semantic search). Without
it, contact-request notifications and embedding generation would fail at
`.delay()`, so any image with 2.2+ requires it.

What the 2.9 Terraform adds (all additive — see `terraform plan`):
- `elasticache.tf` — single-node `cache.t4g.micro` **Redis** in private subnets;
  its SG only accepts 6379 from the tasks SG. ~$12/mo.
- `ecs.tf` — `OPENAI_API_KEY` + `CELERY_BROKER_URL` on the web task; a new
  **worker task definition** (`celery -A config worker`) and **worker service**.
- `secrets.tf` / `iam.tf` — `OPENAI_API_KEY` SSM SecureString + read permission.

**Live-tunable search threshold:** `SEARCH_SIMILARITY_THRESHOLD` is a plain web
task-def env var (`var.search_similarity_threshold`, default 0.4) — adjust the
semantic-search relevance floor without an image rebuild:
`terraform apply -var search_similarity_threshold=N -var image_tag=<current>`.
0.8 is too high for text-embedding-3-small (measured matches ~0.45–0.78); 0 disables.

### First Phase-2 deploy (when ready — spends ~$12/mo more)

```bash
cd infra/terraform
# 1) Put the OpenAI key in terraform.tfvars (git-ignored):
#    openai_api_key = "sk-..."
../scripts/push-image.sh <tag>          # build+push the current main image
terraform apply -var image_tag=<tag>    # creates Redis + worker, updates web task
../scripts/run-migrations.sh            # runs the pgvector migrations (0004/0005);
                                        # CREATE EXTENSION vector runs as the RDS master user
# Verify:
curl "$(terraform output -raw api_url)"                 # web still 200
aws logs tail /ecs/frikkinwave-prod --since 5m | grep worker   # worker booted, ready
```

The worker rides the **same image** as the web task (command override), so one
`push-image.sh` + `apply` updates both. Roll the worker off cheaply any time
with `terraform apply -var worker_desired_count=0`.

### Continuous deployment

**Decision (2.9): staying manual — no CD.** CI lints/tests/migrates only;
deploys are the manual `push-image.sh → terraform apply → run-migrations.sh`
flow above. Rationale: solo prod project, deploys touch real cost + an immutable
prod DB, and the deploy is infrequent — an automated pipeline (plus AWS creds in
CI and migration-safety gating) isn't worth it yet. Revisit if the cadence picks
up; a manual-approval-gated GitHub Actions job is the natural next step.

## Tear down & bring back up (scripted)

Two wrapper scripts make the destroy → restore cycle one command each:

```bash
./infra/scripts/teardown.sh            # destroy app stack, take a final RDS snapshot
./infra/scripts/teardown.sh --wipe     # destroy WITHOUT a snapshot (data lost)

./infra/scripts/bring-up.sh                    # auto-tag (HEAD) + AUTO-restore latest snapshot (fresh if none)
./infra/scripts/bring-up.sh --fresh            # auto-tag, empty DB
./infra/scripts/bring-up.sh <sha>              # pin image, AUTO-restore latest snapshot
./infra/scripts/bring-up.sh <sha> --fresh      # pin image, empty DB
./infra/scripts/bring-up.sh <sha> <snapshot>   # pin image, restore a specific snapshot
```

`bring-up.sh` runs the whole ordered sequence (ECR → push image → full apply →
`run-migrations.sh` → health check). The **image tag defaults to the current git
HEAD short SHA** (push-image.sh builds the working tree and only labels it, so HEAD
is the honest tag); pass an explicit SHA to override. In the default `auto` mode it
queries RDS for the **latest manual snapshot of `frikkinwave-prod-db`** and restores it —
final-on-destroy and hand-taken snapshots are `manual` and survive a destroy
(automated daily backups are deleted with the instance). It prints the resolved
snapshot and asks for confirmation before applying. Override the project/env/region
via `TF_PROJECT` / `TF_ENVIRONMENT` / `AWS_REGION` env vars if they differ from the
defaults.

**Both scripts are safe to re-run.** Terraform is idempotent, so if an apply is
interrupted partway — e.g. a transient local network drop, which surfaces as
`dial tcp: lookup ...: no such host` or `Plugin did not respond` — just run the
same script again and it resumes from state (already-created resources are skipped,
the rest are created). A half-finished bring-up leaves the snapshot untouched.

### Manual equivalents

```bash
cd infra/terraform && terraform destroy   # app stack only — DNS + cert survive
```

By default destroy **takes a final RDS snapshot** (`db_skip_final_snapshot=false`)
named `frikkinwave-prod-final-<rand>` (see the `db_final_snapshot_name` output),
so the data is retained. Restore it on a later apply with
`-var db_snapshot_identifier=<snapshot-id>` (wired in `rds.tf`). Manual snapshots
also survive a destroy. To wipe cleanly instead (no snapshot):
`terraform destroy -var db_skip_final_snapshot=true`.

**Never** `terraform destroy` the `infra/dns` stack unless you intend to give up
the domain delegation — doing so deletes the hosted zone, rotates the
nameservers, and would require re-adding the NS records at GoDaddy.

## Notes / scope

- **State is local** (`terraform.tfstate`, git-ignored, one per stack). Migrate to
  an S3 backend + DynamoDB lock once the account is bootstrapped — see the
  commented block in each `main.tf`.
- **No NAT gateway.** Tasks run in public subnets with public IPs (saves ~$32/mo);
  the task security group still only accepts traffic from the ALB.
- **DNS + HTTPS:** `api.frikkinwave.com` is a Route 53 hosted zone (persistent stack)
  delegated from the parent domain — apex stays with the registrar/Vercel. ACM issues
  the TLS cert (DNS-validated). The app stack's ALB has an HTTPS:443 listener using
  that cert and redirects HTTP:80 → 443. Django sets `SECURE_PROXY_SSL_HEADER` to
  trust the ALB's `X-Forwarded-Proto`.
- **Database:** RDS Postgres 16 (`db.t4g.micro`) in private subnets, not internet-reachable;
  the tasks SG is the only thing allowed to reach it on 5432. `terraform destroy` takes a
  **final snapshot** by default (`db_skip_final_snapshot=false`), so data is retained; a
  clean rebuild uses `run-migrations.sh` on the next apply, or restore the snapshot via
  `snapshot_identifier`. Pass `-var db_skip_final_snapshot=true` to wipe without a snapshot.
- **Secrets:** `DJANGO_SECRET_KEY` and `DATABASE_URL` live in SSM Parameter Store
  (SecureString) and are injected into the container via the task definition's `secrets`
  block. The Terraform-generated DB password never appears in the task definition.
- **ALB health checks + ALLOWED_HOSTS:** ALB health checks reach the container with the
  task's private IP as the Host header. `config/settings/production.py` appends that IP
  (from the ECS metadata endpoint) to `ALLOWED_HOSTS` so `/api/health/` returns 200.
- **ARM64/Graviton** tasks — the push script builds `linux/arm64` to match.
- **Future — Phase 5 Block D (real-time messaging), DEFERRED.** Not built yet. When picked
  up it will need an **infra change** this stack doesn't have: an **ASGI** runtime for
  WebSockets (Django Channels) — current web task is WSGI/gunicorn — most likely a **separate
  ECS service + target group** for WS (keep REST on WSGI), an ALB listener rule that supports
  the WebSocket upgrade (+ stickiness or a stateless channel layer), and a Redis **channel
  layer** (can reuse the existing ElastiCache). Plan this before writing code — see
  ROADMAP.md "Block D" for the open questions.

## Rough cost while running

In `ap-south-1` (Mumbai): ALB ~$18/mo + 1× Fargate (0.25 vCPU/0.5 GB) ~$10/mo +
RDS `db.t4g.micro` ~$13/mo + 20 GB gp3 ~$2/mo + minimal ECR/logs ≈ **~$43/mo**
for the app stack. The persistent dns stack adds the Route 53 zone (~$0.50/mo);
ACM certs are free. `terraform destroy` on the app stack removes the ~$43/mo.
