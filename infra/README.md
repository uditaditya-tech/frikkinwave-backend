# Infrastructure

Two Terraform stacks and one Helm chart. Terraform owns AWS; Helm owns the app.

```
infra/
├── dns/        PERSISTENT — Route 53 zone + ACM cert. Never destroy.
├── eks/        DISPOSABLE — VPC, EKS, RDS, ECR, load balancer controller.
├── helm/       The application chart (web, workers, redis, migrations, ingress).
└── scripts/    eks-up.sh · app-deploy.sh · eks-down.sh
```

> **The detailed runbook is [`eks/README.md`](eks/README.md)** — cost model, the
> traps already paid for, and what each phase built. Read that before changing
> anything here.

---

## Why the split

`dns/` holds the hosted zone and the certificate. Destroying it breaks the
registrar's NS delegation and forces the cert to be reissued, so it is kept
permanently and costs ~$0.50/mo. `eks/` discovers both via `data` sources, which
is what lets the entire application stack be destroyed and recreated freely.

The app is **not** in Terraform. It ships as a Helm release, so a new image is a
~30 second `helm upgrade` rather than a plan/apply across the whole stack — and
it is the natural on-ramp to ArgoCD.

---

## One-time DNS bootstrap

Only needed once, or if the zone is ever lost.

```bash
terraform -chdir=infra/dns init && terraform -chdir=infra/dns apply
```

Take the four nameservers it outputs and add them as `NS` records for `api` at
the parent domain's registrar. Verify the delegation, then the ACM certificate
validates itself a few minutes later:

```bash
dig +short NS api.frikkinwave.com @8.8.8.8
```

---

## Daily use

```bash
./infra/scripts/eks-up.sh
```

~15 min. Cluster, RDS (restored from a snapshot), ECR, load balancer controller.
Needs `infra/eks/terraform.tfvars` — copy `terraform.tfvars.example` and set
`django_secret_key`.

```bash
./infra/scripts/app-deploy.sh
```

~5 min. Builds and pushes the `linux/arm64` image, runs `helm upgrade`, points
Route 53 at the ALB, and verifies the public endpoint actually serves 200.

```bash
./infra/scripts/eks-down.sh
```

Back to $0. Deletes the Kubernetes objects that own AWS resources *before*
destroying, then re-queries AWS and exits non-zero if anything survived — a bare
`terraform destroy` leaves the ALB and any EBS volumes behind, still billing.

**Cost while running: ~$0.20/hr.** Idle: $0, aside from the DNS stack.

---

## Things that will bite you

These are summarised here and explained in [`eks/README.md`](eks/README.md):

- **Keep `kubernetes_version` in standard support.** Extended support is ~6x the
  control-plane price for an identical cluster, and nothing warns you.
- **Node placement can silently collapse into one AZ** when an instance type is
  out of capacity there. The node group spans three subnets and accepts more
  than one instance type for exactly this reason.
- **A restored snapshot gives you the schema, never the derived data.** Anything
  denormalized after the snapshot was taken needs its backfill run by hand.
- **`POD_IP` is load-bearing.** Kubelet probes and ip-mode ALB health checks send
  the pod IP as the `Host` header; without it in `ALLOWED_HOSTS` no pod ever
  reaches Ready.
- **Helm templates are excluded from pre-commit's `check-yaml`** — they are Go
  templates until rendered. `helm lint` and `helm template` cover them.
