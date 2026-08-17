"""Light load test against a running instance — `uv run docflow-loadtest`.

Deliberately over real HTTP against a live server, not in-process like
`scripts/seed.py`. The point here is different: `seed.py` wants the pipeline
without needing a server running; this wants the server — rate limiting,
connection handling, and (specifically) the row-locking fix in
`DocumentService.reprocess` (see `ca3cf58` / docs/ARCHITECTURE.md#concurrency-on-shared-rows)
under more concurrent contention than the two-connection unit test exercises.

Three checks, each printed as its own report:

1. **Concurrent uploads** — N uploads fired at once. Reports success rate and
   latency distribution. Confirms the upload path doesn't fall over or corrupt
   state under concurrent multipart requests.
2. **General API throughput** — M requests to a cheap, read-only endpoint.
   Reports p50/p95/p99 latency and error rate.
3. **Concurrent reprocess on one document** — the important one. Uploads a
   document, waits for it to reach a terminal state, then fires K reprocess
   requests at the *same* document simultaneously. Exactly one must succeed
   (202) and the rest must be refused (409) — if more than one succeeds, the
   concurrency protection has regressed and this script exits non-zero.

Not a replacement for a real load-testing tool (Locust, k6) at meaningfully
higher scale — this is the "reasonable basis" version: enough to catch a
regression in the specific properties this project has already had bugs in,
not a capacity-planning benchmark. See docs/PRODUCTION_READINESS.md.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import statistics
import time
import uuid
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"


@dataclass
class Timing:
    label: str
    status: int
    elapsed_ms: float


@dataclass
class Report:
    timings: list[Timing] = field(default_factory=list)

    def add(self, label: str, status: int, elapsed_ms: float) -> None:
        self.timings.append(Timing(label, status, elapsed_ms))

    def summary(self) -> dict[str, float | int]:
        durations = sorted(t.elapsed_ms for t in self.timings)
        ok = sum(1 for t in self.timings if 200 <= t.status < 300 or t.status == 202)
        n = len(durations)
        return {
            "count": n,
            "ok": ok,
            "errors": n - ok,
            "mean_ms": round(statistics.mean(durations), 1) if durations else 0.0,
            "p50_ms": round(durations[int(n * 0.50)], 1) if n else 0.0,
            "p95_ms": round(durations[min(n - 1, int(n * 0.95))], 1) if n else 0.0,
            "p99_ms": round(durations[min(n - 1, int(n * 0.99))], 1) if n else 0.0,
        }


def _invoice_bytes(tag: str) -> bytes:
    return (
        f"INVOICE\n\nInvoice No.: LOAD-{tag}\nIssue Date: 2026-08-17\n"
        f"Supplier: Load Test Supplier s.r.o.\nSubtotal: 100.00 EUR\n"
        f"Tax: 21.00 EUR\nTotal: 121.00 EUR\nCurrency: EUR\n"
    ).encode()


async def _register(client: httpx.AsyncClient, tag: str) -> str:
    email = f"loadtest-{tag}-{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        f"{API_PREFIX}/auth/register",
        json={
            "email": email,
            "password": "a-sufficiently-long-password",
            "full_name": "Load Test",
            "organization_name": f"Load Test {tag}",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def _timed_request(
    client: httpx.AsyncClient, report: Report, label: str, method: str, url: str, **kwargs: object
) -> httpx.Response:
    started = time.perf_counter()
    response = await client.request(method, url, **kwargs)  # type: ignore[arg-type]
    elapsed_ms = (time.perf_counter() - started) * 1000
    report.add(label, response.status_code, elapsed_ms)
    return response


async def concurrent_uploads(client: httpx.AsyncClient, token: str, n: int) -> Report:
    print(f"\n=== 1. {n} concurrent uploads ===")
    headers = {"Authorization": f"Bearer {token}"}
    report = Report()

    async def upload(i: int) -> None:
        files = {"file": (f"load-{i}.txt", io.BytesIO(_invoice_bytes(str(i))), "text/plain")}
        await _timed_request(
            client,
            report,
            "upload",
            "POST",
            f"{API_PREFIX}/documents",
            headers=headers,
            files=files,
        )

    await asyncio.gather(*(upload(i) for i in range(n)))
    _print_summary(report)
    return report


async def general_throughput(client: httpx.AsyncClient, token: str, n: int) -> Report:
    print(f"\n=== 2. {n} general API requests (GET /documents) ===")
    headers = {"Authorization": f"Bearer {token}"}
    report = Report()

    async def get_documents(_: int) -> None:
        await _timed_request(
            client, report, "list", "GET", f"{API_PREFIX}/documents", headers=headers
        )

    await asyncio.gather(*(get_documents(i) for i in range(n)))
    _print_summary(report)
    return report


async def concurrent_reprocess(client: httpx.AsyncClient, token: str, k: int) -> bool:
    print(f"\n=== 3. {k} concurrent reprocess attempts on one document ===")
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("reprocess-target.txt", io.BytesIO(_invoice_bytes("target")), "text/plain")}
    upload_resp = await client.post(f"{API_PREFIX}/documents", headers=headers, files=files)
    upload_resp.raise_for_status()
    document_id = upload_resp.json()["document_id"]

    # Wait for the initial processing to leave queued/processing — reprocess
    # refuses while a document is already in flight, which would confound
    # this specific test (it's testing the race between reprocess calls, not
    # the "already in flight" refusal against the original job).
    for _ in range(30):
        detail = await client.get(f"{API_PREFIX}/documents/{document_id}", headers=headers)
        status_value = detail.json()["status"]
        if status_value not in ("queued", "processing"):
            break
        await asyncio.sleep(0.5)
    else:
        print(
            f"  Document never left queued/processing (status={status_value}); aborting this check."
        )
        return False
    print(
        f"  Document {document_id} reached '{status_value}'; firing {k} concurrent reprocess calls..."
    )

    report = Report()

    async def reprocess(_: int) -> None:
        await _timed_request(
            client,
            report,
            "reprocess",
            "POST",
            f"{API_PREFIX}/documents/{document_id}/reprocess",
            headers=headers,
        )

    await asyncio.gather(*(reprocess(i) for i in range(k)))

    accepted = [t for t in report.timings if t.status == 202]
    conflicts = [t for t in report.timings if t.status == 409]
    other = [t for t in report.timings if t.status not in (202, 409)]

    print(f"  202 Accepted: {len(accepted)}")
    print(f"  409 Conflict: {len(conflicts)}")
    if other:
        print(f"  Other status codes: {[t.status for t in other]}")
    _print_summary(report)

    ok = len(accepted) == 1 and len(conflicts) == k - 1
    verdict = "PASS" if ok else "FAIL"
    print(
        f"  [{verdict}] expected exactly 1 accepted + {k - 1} conflicts "
        f"(the row-lock fix from ca3cf58 holding under {k}-way contention, not just 2)"
    )
    return ok


def _print_summary(report: Report) -> None:
    s = report.summary()
    print(
        f"  {s['count']} requests · {s['ok']} ok · {s['errors']} errors · "
        f"mean {s['mean_ms']}ms · p50 {s['p50_ms']}ms · p95 {s['p95_ms']}ms · p99 {s['p99_ms']}ms"
    )


async def _run(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        token = await _register(client, "main")

        await concurrent_uploads(client, token, args.uploads)
        await general_throughput(client, token, args.requests)
        reprocess_ok = await concurrent_reprocess(client, token, args.reprocess)

    print("\n=== Summary ===")
    print(f"Concurrency protection: {'PASS' if reprocess_ok else 'FAIL'}")
    return 0 if reprocess_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--uploads", type=int, default=10, help="Concurrent uploads (check 1)")
    parser.add_argument("--requests", type=int, default=50, help="General API requests (check 2)")
    parser.add_argument(
        "--reprocess", type=int, default=10, help="Concurrent reprocess calls (check 3)"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
