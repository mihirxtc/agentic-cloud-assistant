import json
import os
import statistics
import time
from datetime import datetime, timezone

import httpx

BASE_URL = "http://localhost:8000"

CREDENTIALS = {
    # LLM key used for /chat, /security, /cost endpoints.
    # Leave blank ("") to fall back to the GROQ_API_KEY in your .env file.
    "groq_api_key": os.getenv("GROQ_API_KEY", ""),
    # AWS region passed to /scan and /security as a query parameter.
    "aws_region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
}

RUNS = 3

TIMEOUT = 60


def _call(fn):
    """Call fn() once, return (elapsed_seconds, httpx.Response)."""
    t0 = time.perf_counter()
    resp = fn()
    elapsed = time.perf_counter() - t0
    return elapsed, resp


def benchmark(label: str, fn, runs: int = RUNS) -> dict:
    """
    Call fn() `runs` times, collect timings, return a stats dict.

    fn must be a zero-argument callable that returns an httpx.Response.
    Each run is printed immediately so the user can see live progress.
    """
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print(f"{'─' * 60}")

    times = []
    last_status = None
    last_body = None

    for i in range(1, runs + 1):
        try:
            elapsed, resp = _call(fn)
            times.append(elapsed)
            last_status = resp.status_code
            # Show a short extract of the response body for sanity-checking
            try:
                body = resp.json()
                preview = json.dumps(body)[:120]
                if len(json.dumps(body)) > 120:
                    preview += "…"
            except Exception:
                preview = resp.text[:120]
            last_body = preview
            print(f"  run {i}/{runs}  {elapsed:6.3f}s  HTTP {last_status}")
        except Exception as exc:
            print(f"  run {i}/{runs}  ERROR: {exc}")
            times.append(None)

    good = [t for t in times if t is not None]

    if good:
        stats = {
            "min_s": round(min(good), 3),
            "max_s": round(max(good), 3),
            "mean_s": round(statistics.mean(good), 3),
            "median_s": round(statistics.median(good), 3),
        }
        print(
            f"\n  min={stats['min_s']}s  max={stats['max_s']}s  "
            f"mean={stats['mean_s']}s  median={stats['median_s']}s"
        )
        print(f"  last response preview: {last_body}")
    else:
        stats = {"min_s": None, "max_s": None, "mean_s": None, "median_s": None}
        print("  All runs failed — no timing stats available.")

    return {
        "endpoint": label,
        "runs": runs,
        "successful": len(good),
        "status_code": last_status,
        "raw_s": [round(t, 3) if t is not None else None for t in times],
        **stats,
    }


def run_all_benchmarks() -> list:
    """Define and run every benchmark. Returns list of result dicts."""

    groq_key = CREDENTIALS["groq_api_key"]
    region = CREDENTIALS["aws_region"]

    with httpx.Client(timeout=TIMEOUT) as client:
        results = []

        results.append(
            benchmark(
                "GET /health",
                lambda: client.get(f"{BASE_URL}/health"),
            )
        )

        results.append(
            benchmark(
                "GET /scan",
                lambda: client.get(
                    f"{BASE_URL}/scan",
                    params={"region": region},
                ),
            )
        )

        results.append(
            benchmark(
                "GET /security",
                lambda: client.get(
                    f"{BASE_URL}/security",
                    params={
                        "region": region,
                        "model": "groq",
                        "api_key": groq_key,
                    },
                ),
            )
        )

        results.append(
            benchmark(
                "GET /cost",
                lambda: client.get(
                    f"{BASE_URL}/cost",
                    params={
                        "model": "groq",
                        "api_key": groq_key,
                    },
                ),
            )
        )

        results.append(
            benchmark(
                "POST /chat (groq, 'List my EC2 instances')",
                lambda: client.post(
                    f"{BASE_URL}/chat",
                    json={
                        "message": "List my EC2 instances",
                        "model": "groq",
                        "api_key": groq_key,
                        "history": [],
                    },
                ),
            )
        )

        results.append(
            benchmark(
                "POST /terraform/generate (groq, secure S3 bucket)",
                lambda: client.post(
                    f"{BASE_URL}/terraform/generate",
                    json={
                        "request": "Create a secure S3 bucket with encryption enabled",
                        "model": "groq",
                        "api_key": groq_key,
                    },
                ),
            )
        )

    return results


def print_summary(results: list) -> None:
    """Print a compact summary table after all benchmarks complete."""

    print(f"\n{'═' * 60}")
    print("  BENCHMARK SUMMARY")
    print(f"{'═' * 60}")
    print(f"  {'Endpoint':<45} {'mean':>7}  {'median':>7}  {'min':>7}  {'max':>7}")
    print(f"  {'-' * 45}  {'-' * 7}  {'-' * 7}  {'-' * 7}  {'-' * 7}")

    for r in results:
        name = r["endpoint"]
        if len(name) > 45:
            name = name[:42] + "…"
        mean = f"{r['mean_s']:>7.3f}s" if r["mean_s"] is not None else "  N/A   "
        median = f"{r['median_s']:>7.3f}s" if r["median_s"] is not None else "  N/A   "
        lo = f"{r['min_s']:>7.3f}s" if r["min_s"] is not None else "  N/A   "
        hi = f"{r['max_s']:>7.3f}s" if r["max_s"] is not None else "  N/A   "
        print(f"  {name:<45}  {mean}  {median}  {lo}  {hi}")

    print(f"{'═' * 60}\n")


def save_results(results: list, path: str = "benchmark_results.json") -> None:
    """Write results + metadata to JSON."""

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "runs_per_endpoint": RUNS,
        "timeout_s": TIMEOUT,
        "results": results,
    }

    with open(path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"  Results saved → {path}")


if __name__ == "__main__":
    print(f"\n{'═' * 60}")
    print(f"  Agentic Cloud Assistant — Endpoint Benchmark")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Target: {BASE_URL}   Runs per endpoint: {RUNS}")
    print(
        f"  Region: {CREDENTIALS['aws_region']}   LLM key: {'set' if CREDENTIALS['groq_api_key'] else 'NOT SET — will use server .env'}"
    )
    print(f"{'═' * 60}")

    results = run_all_benchmarks()

    print_summary(results)
    save_results(results)
