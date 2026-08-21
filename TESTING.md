# TESTING.md — coverage audit, gaps, and measured capacity

Written 2026-08-21 against commits `9f0ac79`..`dee3465`, with every load number
measured on the live `frikkinwave-prod` cluster the same day. Numbers here are
**measured, not estimated** — anything inferred says so.

---

## 1. Where the suite stands

**377 tests in 33 files, green in 4.13s.**

The speed is not an accident: `EVENT_RELAY_INLINE=True` under pytest delivers events
to in-process subscribers instead of a broker, so the whole event pipeline is
exercised without Kafka. That is what keeps CI keyless and network-free.

| area | files | what they cover |
|---|---|---|
| Domain apps | 22 | happy + negative path per endpoint, permissions, validation |
| Events / outbox | 4 | relay, retries, DLT, consumer dispatch, relay health gauges |
| Architecture | 1 | cross-app imports, `publish()` discipline, topic/consumer symmetry |
| Infrastructure | 1 | Helm/Terraform invariants, ACLs, CRD ordering, alert thresholds |

### What is genuinely well covered

- **Delivery semantics.** Dead-lettering, bounded retry, transient-failure retry,
  and "every failure path commits its offset" are all tested.
- **Idempotency** at four independent sites (`relay_pending`, search indexing,
  rating propagation, re-follow).
- **Architectural rules as tests, not discipline** — a runtime cross-app model
  import or an unsubscribed topic fails the build.
- **N+1 avoidance in practice.** All eight list services use
  `select_related`/`prefetch_related` (29 usages).

---

## 2. Gaps, and the test cases that close them

Ordered by what would actually bite first.

### Gap A — N+1 protection is correct but unguarded

29 `select_related` calls, **one** query-count assertion in the whole suite
(`apps/musicians/tests/test_profile_rating.py:61`). Delete any `select_related`
and nothing fails; the endpoint just gets slower in production.

| # | test case | type |
|---|---|---|
| A1 | `GET /api/listings/` with 100 listings issues a constant number of queries regardless of row count | integration, `django_assert_num_queries` |
| A2 | same for `/api/venues/`, `/api/bands/`, `/api/reviews/<user>/`, `/api/social/following/` | integration |
| A3 | `/api/engagements/` and `/api/connections/` (both `select_related` two FKs) hold constant across box=`inbox`/`outbox` | integration |
| A4 | band detail with 50 members does not scale queries with member count — `views.py:109` materialises `list(list_band_members(...))` | integration |

Write these as a parametrised test over (endpoint, factory, expected_queries).
The point is not the exact number; it is that the number does not grow with rows.

### Gap B — no concurrency tests anywhere

`relay_pending` claims rows with `select_for_update(skip_locked=True)`, which is
correct and is what makes >1 relay replica safe. **Nothing tests it.** The relay
runs at 1 replica today, so the day someone scales it is the day this is first
exercised — in production.

| # | test case | type |
|---|---|---|
| B1 | two concurrent `relay_pending()` calls over the same pending set publish each event exactly once | integration, threads + real DB |
| B2 | a relay that dies mid-dispatch redelivers on the next run (at-least-once holds) | integration |
| B3 | concurrent `POST /api/reviews/` for the same (author, context) hits the unique constraint, not a double row | integration |
| B4 | concurrent follow/unfollow of the same pair converges to one consistent state | integration |

B1 is the important one and needs real threads against a real connection —
`pytest-django`'s `TransactionTestCase` semantics, not the default `db` fixture.

### Gap C — consumer rebalancing is untested

Documented as a known gap and still open. A rollout revokes and reassigns
partitions; an in-flight message at that moment is the classic duplicate source.

| # | test case | type |
|---|---|---|
| C1 | a consumer killed between handler success and offset commit reprocesses on restart without corrupting state | integration |
| C2 | two consumers in one group split partitions and neither double-handles | integration, needs a broker |
| C3 | rolling restart of a consumer Deployment under sustained produce loses nothing and duplicates only idempotently | **drill**, not CI |

C3 belongs in a runbook drill like the Kafka failure drill, not the unit suite.

### Gap D — no load or performance regression tests

Until this session there were none. Section 3 records the baselines; nothing
enforces them. A CONN_MAX_AGE regression or a lost index would not be caught.

| # | test case | type |
|---|---|---|
| D1 | assert `CONN_MAX_AGE` is set in production settings (guards the fix in §4) | unit, settings |
| D2 | assert every list endpoint declares a cursor paginator | architecture test |
| D3 | smoke: `/api/health/` p95 under 50ms at c=10 in-cluster | drill |

### Gap E — no rate limiting exists to test

`REST_FRAMEWORK` sets no `DEFAULT_THROTTLE_CLASSES`. There is no per-user or
per-IP limit anywhere, so one client can consume the entire 6-worker pool. This
is a **missing feature**, not just a missing test — tests come with it.

| # | test case | type |
|---|---|---|
| E1 | an unauthenticated client exceeding the anon rate gets 429 with `Retry-After` | integration |
| E2 | authenticated users get the higher authenticated rate | integration |
| E3 | `/api/health/` is exempt so probes are never throttled | integration |

E3 matters: throttling the health path would make the ALB pull healthy pods.

---

## 3. Measured capacity (2026-08-21, live cluster)

Load generated **inside the cluster** against the `frikkinwave-web` Service, so
these numbers are the application's, not the WAN's. 2 web pods × 3 sync gunicorn
workers, `db.t4g.micro`.

### `/api/health/` — no database

| concurrency | rps | p50 | p95 | p99 | errors |
|---|---|---|---|---|---|
| 1 | 154 | 6.4ms | 8.3ms | 9.6ms | 0 |
| 10 | **631** | 14.9ms | 25.5ms | 32.9ms | 0 |
| 50 | 622 | 79ms | 144ms | 158ms | 0 |
| 100 | 574 | 168ms | 324ms | 353ms | 0 |
| 400 | 426 | 953ms | 1170ms | 1308ms | 0 |
| 800 | 337 | 2375ms | 2879ms | 3031ms | 0 |

### `/api/listings/` — cursor-paginated DB read

Measured twice: before the `CONN_MAX_AGE` fix, and after it (commit `dee3465`).

| concurrency | rps before → after | p50 before → after | p99 before → after |
|---|---|---|---|
| 1 | 28 → **82** | 36.3ms → **12.1ms** | 75ms → **39ms** |
| 10 | 66 → **154** | 166ms → **22ms** | 309ms → **180ms** |
| 50 | 68 → **154** | 492ms → **128ms** | 1542ms → **707ms** |
| 100 | 64 → **147** | 1158ms → **468ms** | 2963ms → **1376ms** |
| 400 | 77 | 4030ms | 7858ms | *(before only)* |

**~2.3x throughput and ~3x lower latency, from one setting.** `/api/health/`
re-measured at 657 rps against a 631 rps baseline — unchanged, as expected for a
path that never touches the database.

### Event pipeline

1000 events enqueued in 126ms, drained outbox → Kafka → consumer in ~8s:
**~125 events/sec** (poll granularity was 2s, so the true rate is 125–165/sec).
Supersedes the earlier ~87/sec figure. Consumer kept up; lag returned to 0.

### What the load did to the system

- **Zero errors at every level, up to c=800.** Nothing 5xx'd, nothing refused.
- **Zero pod restarts.** No OOM, no crashloop.
- RDS CPU peaked at **47%**; DB connections never exceeded **6**.
- Web containers throttled **89%** and **34.5%** of CFS periods.
- `CPUThrottlingHigh` fired correctly — the alerting caught it.

---

## 4. What actually limits throughput

**Not the database, and not the connection count.** Three findings, in order of
leverage:

### 4.1 Connection setup costs 29× the query it runs

Measured from a web pod:

| operation | p50 |
|---|---|
| open a new Postgres connection | **16.9ms** |
| the query itself, on a reused connection | **0.58ms** |

`CONN_MAX_AGE` was unset, so Django's default of `0` closed the connection after
every request and paid that 16.9ms again on the next one. That was most of the
36ms floor on every DB-backed request.

**Fixed in `dee3465`** (`CONN_MAX_AGE=60` + `CONN_HEALTH_CHECKS`), and the gain
was verified by re-running the same load: see the before/after table in §3. A
test in `tests/test_architecture.py` guards it, because a revert is invisible in
review and invisible to the unit suite — only a load test shows it.

Peak concurrent connections during the whole test was **6** — one per gunicorn
worker, against a `t4g.micro` ceiling of roughly 112. So persistent connections
are safe here with no pooler; the risk only appears when
`pods × workers` approaches `max_connections`, which is where PgBouncer belongs.

### 4.2 CPU limits, via CFS throttling, set the real ceiling

A pod averaging 0.393 of its 0.500-core limit was still throttled **89% of CFS
periods**, because the limit is enforced per 100ms window and request handling is
bursty. Average headroom hides it; only the throttling metric shows it.

### 4.3 Under overload the system queues instead of shedding

At c=800 there were no errors — requests simply waited up to 3s (7.9s on the DB
path). There is no rate limiting, no explicit gunicorn `--timeout`, and **no HPA**
(web is pinned at 2 replicas). A p99 of 7.9s is an outage the error rate cannot
see, and the system cannot scale out of it on its own.

### 4.4 Two traps that corrupt these measurements

Both were hit while producing the numbers above, and both make a result look
like a code regression when it is an artefact of where things ran.

**Co-locating the generator with the thing it measures.** The first "after" run
put the loadgen pod on the same node as a web pod. `/api/health/` appeared to
drop from 631 to 369 rps — a 40% regression from a change that does not touch the
database. Pinning the generator to a node running no web pods restored 657 rps.
Pin it explicitly (`nodeName`) rather than trusting the scheduler.

**The node pool is deliberately mixed.** Two `m7g.large` plus one `t4g.large` —
different capacity pools on purpose, after t4g went short across all three AZs on
2026-08-20 and stranded the node group. The performance consequence is that an
identical pod gets non-burstable CPU on `m7g` and *burstable, credit-limited* CPU
on `t4g`. Sustained load on the t4g node drains credits and slows down over time,
so a long run is not comparable to a short one unless placement is held fixed.

## 5. Recommended order

1. ~~**Set `CONN_MAX_AGE`** (+ test D1).~~ **Done in `dee3465`** — 2.3x
   throughput, 3x lower latency, verified by re-measurement.
2. **Raise or remove the web CPU limit** — keep the request, since throttling
   at 89% while below the limit is pure waste.
3. **Add the query-count tests (A1–A4).** Cheap, and they guard work already done.
4. **Add throttling (E1–E3)** — the only protection against a single abusive client.
5. **Add an HPA** on CPU, once limits are sane.
6. **Test relay concurrency (B1)** before anyone scales the relay past 1 replica.
