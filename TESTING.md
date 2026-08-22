# TESTING.md — coverage audit, gaps, and measured capacity

Written 2026-08-21 against commits `9f0ac79`..`dee3465`, with every load number
measured on the live `frikkinwave-prod` cluster the same day. Numbers here are
**measured, not estimated** — anything inferred says so.

> **Updated 2026-08-23** for the OpenSearch migration (ROADMAP Phase 2R). The
> capacity numbers below are unchanged and still describe the last live cluster;
> that cluster ran pgvector and OpenAI, neither of which exists now, and nothing
> has been deployed since. **Treat every load figure here as historical until the
> stack is rebuilt and re-measured.** The suite counts are current.

---

## 1. Where the suite stands

**409 tests in 33 files, green in ~5.5s** with a local OpenSearch container —
**389 with 20 skipped** without one.

Those 20 are the only tests in the repo that need a live dependency. `local.py`
blanks `OPENSEARCH_URL` under pytest so the rest of the suite never touches a
cluster (otherwise every profile-save test would make an HTTP call), and the
search tests opt in through `OPENSEARCH_TEST_URL`. Skipping is a local
convenience and would be a silent hole in CI, so `tests/test_architecture.py`
asserts the workflow sets it.

The speed is not an accident: `EVENT_RELAY_INLINE=True` under pytest delivers events
to in-process subscribers instead of a broker, so the whole event pipeline is
exercised without Kafka.

Search is the one place that pattern was **deliberately not** repeated. Its tests
run in two tiers: a spy client proves the wiring (a filter is asked for, a limit
is passed, an unreachable cluster degrades), and ~20 tests run against a real
cluster for everything that is a claim about OpenSearch rather than about our
code — that the analyzer stems, that the boosts order results as the mapping
says, that `dynamic: strict` rejects an unknown field. A fake could be made to
pass all of those and would prove nothing.

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
- **The two failure modes that report success.** Both were found by running
  things by hand, not by tests, and both now have regression tests: a
  `delete_by_query` version conflict aborting the index sweep mid-batch, and
  `reindex_profiles` printing "Indexed 42 profiles." over a cluster it was never
  configured to reach.
- **Architectural rules as tests, not discipline** — a runtime cross-app model
  import or an unsubscribed topic fails the build.
- **N+1 avoidance in practice.** All eight list services use
  `select_related`/`prefetch_related` (29 usages).

---

## 2. Gaps, and the test cases that close them

Ordered by what would actually bite first.

### Gap A — N+1 protection is correct but unguarded

**CLOSED** — `tests/test_query_counts.py` asserts, for five list endpoints, that
the query count does not grow with the number of rows returned (2 rows vs 12).

**It found a real N+1 on its first run.** `/api/reviews/<username>/` issued 4
queries for 2 reviews and 14 for 12 — `list_reviews_for` selected `author` but
`ReviewReadSerializer` also renders `subject.username`. Every row in that
queryset has the *same* subject, which is exactly why selecting it looks
redundant and is not. Fixed alongside the test.

Worth noting how it was missed: an audit grep reported this endpoint as "OK"
because a 22-line window after the function caught a *neighbouring* function's
`select_related`. The test found in one run what reading had gotten wrong.

Cost of the bug, measured under load: **~3x throughput on that endpoint** — see
§3. The test found it; the load test priced it.

| # | test case | type |
|---|---|---|
| A1 | `GET /api/listings/` with 100 listings issues a constant number of queries regardless of row count | integration, `django_assert_num_queries` |
| A2 | same for `/api/venues/`, `/api/bands/`, `/api/reviews/<user>/`, `/api/social/following/` | integration |
| A3 | `/api/engagements/` and `/api/connections/` (both `select_related` two FKs) hold constant across box=`inbox`/`outbox` | integration |
| A4 | band detail with 50 members does not scale queries with member count — `views.py:109` materialises `list(list_band_members(...))` | integration |

Write these as a parametrised test over (endpoint, factory, expected_queries).
The point is not the exact number; it is that the number does not grow with rows.

### Gap B — no concurrency tests anywhere

**B1–B3 CLOSED** — `tests/test_concurrency.py`, using `django_db(transaction=True)`
because the default fixture's uncommitted transaction would make every one of
these pass without exercising anything.

Each guard was verified to *fail* when the thing it protects is removed —
deleting the `select_for_update(skip_locked=True)` claim makes B1 fail, which is
the only evidence that a concurrency test is not passing trivially.

B4 (concurrent follow/unfollow convergence) is still open.

| # | test case | type |
|---|---|---|
| B1 | two concurrent `relay_pending()` calls over the same pending set publish each event exactly once | integration, threads + real DB |
| B2 | a relay that dies mid-dispatch redelivers on the next run (at-least-once holds) | integration |
| B3 | concurrent `POST /api/reviews/` for the same (author, context) hits the unique constraint, not a double row | integration |
| B4 | concurrent follow/unfollow of the same pair converges to one consistent state | integration |

B1 is the important one: without the row claim, two relays both dispatch every
row, and the outbox looks perfectly clean while every consumer sees each event
N times.

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

Section 3 records the baselines. D1 and D2 now guard the two regressions that
would be invisible in review; a lost index still would not be caught.

| # | test case | type |
|---|---|---|
| D1 | ~~assert `CONN_MAX_AGE` is set~~ — **done**, `tests/test_architecture.py` | unit, settings |
| D2 | ~~assert every list endpoint paginates~~ — **done**; four deliberate exemptions are named with reasons | architecture test |
| D3 | smoke: `/api/health/` p95 under 50ms at c=10 in-cluster | drill, still open |

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

### `/api/reviews/<username>/` — the N+1, measured

The guard in `tests/test_query_counts.py` proved the N+1 existed; this is what it
cost. Same cluster, same generator placement, 24 reviews on the subject so a page
returns the full 20 rows — meaning ~20 extra queries per request before the fix.

| concurrency | rps before → after | p50 before → after | p99 before → after |
|---|---|---|---|
| 1 | 25 → **74** | 45.9ms → **13.1ms** | 120ms → **22ms** |
| 10 | 39 → **127** | 384ms → **44ms** | 532ms → **210ms** |
| 50 | 41 → **130** | 697ms → **196ms** | 2400ms → **800ms** |
| 100 | 40 → **122** | 1344ms → **525ms** | 4933ms → **1669ms** |

**~3x throughput and ~3.5x lower latency from one `select_related` argument.**
Before the fix this endpoint ran at a third of `/api/listings/` (40 vs 154 rps);
after it, the two are comparable. A test caught it, but only a load test says
what it was worth.

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
3. ~~**Add the query-count tests (A1–A4).**~~ **Done** — and they found a real
   N+1 in the reviews list on the first run.
4. ~~**Test relay concurrency (B1).**~~ **Done**, and verified to fail without
   the row claim.
5. **Add throttling (E1–E3)** — still the only protection against a single
   abusive client, and the largest remaining gap. Needs the feature, not just tests.
6. **Add an HPA** on CPU, once limits are sane.
