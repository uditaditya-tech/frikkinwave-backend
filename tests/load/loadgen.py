"""
In-cluster HTTP load generator.

NOT collected by pytest (the filename matches neither `test_*.py` nor `*_test.py`).
This is an operational tool, run by hand against a live cluster.

WHY IN-CLUSTER: running this from a laptop measures the internet — home uplink,
TLS handshakes to Mumbai, and ALB queueing — and calls the result "the API". Run
against the ClusterIP Service instead and the number is the application's.

    REPO=$(terraform -chdir=infra/eks output -raw ecr_repository_url)
    kubectl run loadgen -n frikkinwave --restart=Never \
      --image=$REPO:$(git rev-parse --short HEAD) --command -- sleep 7200
    kubectl cp tests/load/loadgen.py frikkinwave/loadgen:/tmp/loadgen.py
    kubectl exec -n frikkinwave loadgen -- \
      python /tmp/loadgen.py /api/listings/ 12 1,5,10,25,50,100
    kubectl delete pod loadgen -n frikkinwave

Give the pod its own CPU (`--overrides` with a 1500m limit) or the generator
throttles before the app does and you measure the generator.

Baselines and the analysis behind them live in TESTING.md.
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "http://frikkinwave-web.frikkinwave.svc.cluster.local"

# Host: ALLOWED_HOSTS does not contain the Service DNS name, so without this every
# request is a 400.
#
# X-Forwarded-Proto: production sets SECURE_SSL_REDIRECT, and only /api/health/ is
# exempt. In-cluster plain HTTP therefore 301s on every other path. The ALB
# normally supplies this header; emulating it is what makes the test hit the same
# code path a real request does, rather than measuring redirects.
HEADERS = {"Host": "api.frikkinwave.com", "X-Forwarded-Proto": "https"}


async def _worker(
    client: httpx.AsyncClient,
    path: str,
    stop_at: float,
    latencies: list[float],
    errors: dict[str, int],
) -> None:
    while time.monotonic() < stop_at:
        started = time.monotonic()
        try:
            response = await client.get(BASE + path, headers=HEADERS)
            latencies.append((time.monotonic() - started) * 1000)
            if response.status_code != 200:
                key = str(response.status_code)
                errors[key] = errors.get(key, 0) + 1
        except Exception as exc:
            key = type(exc).__name__
            errors[key] = errors.get(key, 0) + 1


async def _phase(path: str, concurrency: int, seconds: int) -> None:
    latencies: list[float] = []
    errors: dict[str, int] = {}
    limits = httpx.Limits(
        max_connections=concurrency * 2,
        max_keepalive_connections=concurrency * 2,
    )
    async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:
        stop_at = time.monotonic() + seconds
        started = time.monotonic()
        await asyncio.gather(
            *[_worker(client, path, stop_at, latencies, errors) for _ in range(concurrency)]
        )
        elapsed = time.monotonic() - started

    if not latencies:
        print(f"  c={concurrency:<4} NO SUCCESSFUL REQUESTS  errs={errors}")
        return

    latencies.sort()

    def pct(q: float) -> float:
        return latencies[min(int(len(latencies) * q), len(latencies) - 1)]

    print(
        f"  c={concurrency:<4} rps={len(latencies) / elapsed:8.1f}  "
        f"p50={pct(0.50):7.1f}ms  p95={pct(0.95):7.1f}ms  p99={pct(0.99):7.1f}ms  "
        f"max={latencies[-1]:7.1f}ms  n={len(latencies):<6} errs={errors or '-'}"
    )


async def main() -> None:
    path = sys.argv[1]
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    levels = (
        [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [1, 5, 10, 25, 50, 100]
    )
    print(f"== {path}  ({seconds}s per level) ==")
    for concurrency in levels:
        await _phase(path, concurrency, seconds)


if __name__ == "__main__":
    asyncio.run(main())
