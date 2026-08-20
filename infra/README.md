# Infrastructure

Two Terraform stacks and one Helm chart. Terraform owns AWS; Helm owns the app.

```
infra/
├── dns/        PERSISTENT — Route 53 zone + ACM cert. Never destroy.
├── eks/        DISPOSABLE — VPC, EKS, RDS, ECR, load balancer controller.
├── helm/       Two charts: the app (web, relay, consumer groups, migrations,
│            ingress) and the Kafka cluster (Strimzi CRs, topics, KafkaUser).
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

This stack is **persistent and applied first**. Besides the zone and the
certificate it owns the budget alarm and the SNS alert topic — everything that
has to outlive a teardown of the app stack. `eks-up.sh` refuses to run until it
has been applied, because the app stack reads all three via data sources.

After the first apply, **check your email and click the SNS confirmation link.**
An unconfirmed subscription accepts publishes and delivers nothing, which looks
exactly like a working alert route.

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
- **After a rebuild your kubeconfig points at the DEAD cluster.** The stack gets a
  new API endpoint every time, so `terraform apply` and `kubectl` fail with
  `no such host` naming the *previous* one. Run `aws eks update-kubeconfig` first,
  then re-apply.
- **A torn-down stack poisons DNS caches for `api.frikkinwave.com`.** Teardown
  deletes the Route 53 alias by design, so anything resolving the name during the
  gap caches a negative answer — and home routers routinely hold it past the
  600s TTL. The site then looks down from that one network while being perfectly
  healthy everywhere else. Check against a public resolver before debugging the
  cluster: `dig +short @1.1.1.1 api.frikkinwave.com`.
- **`helm upgrade --reuse-values` silently drops NEW chart defaults.** It reuses
  the previous release's coalesced values without re-reading the chart, so a key
  added in the same change renders empty and the field is dropped — while helm
  reports success. Use `--reset-then-reuse-values`.
- **A ConfigMap change restarts nothing on its own.** `envFrom` values are
  injected at container start; the chart stamps `checksum/config` on the web,
  web, relay and consumer pod templates so a config change actually rolls them.
