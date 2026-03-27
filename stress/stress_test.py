#!/usr/bin/env python3
"""
Agent OS — 完整压力测试（可提交到 Git，在任意机器 git pull 后运行）

特点：
  - 仅标准库，无额外 pip 依赖（项目已含 pytest 等不影响本脚本）
  - 有界并发：固定总请求数，可复现
  - 可选 API Key：未设置时跳过 /memory/* 与需鉴权的 /health/extended
  - 失败时非零退出码，便于 CI / cron

用法（在仓库根目录）:
  ./stress/run.sh
  BASE_URL=http://127.0.0.1:8000 API_KEY_OWNER=xxx ./stress/run.sh

  # 或直接
  python3 stress/stress_test.py

环境变量见 stress/env.example
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(os.getenv("STRESS_RESULTS_DIR", str(_REPO_ROOT / "stress" / "results")))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Agent OS bounded stress test (stdlib HTTP client).",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://127.0.0.1:8000"),
        help="API base URL (env BASE_URL)",
    )
    p.add_argument(
        "--requests",
        type=int,
        default=_env_int("STRESS_TOTAL_REQUESTS", 2000),
        help="Total requests per GET/POST load phase (env STRESS_TOTAL_REQUESTS)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=_env_int("STRESS_CONCURRENCY", 20),
        help="Thread pool size (env STRESS_CONCURRENCY)",
    )
    p.add_argument(
        "--register-count",
        type=int,
        default=_env_int("STRESS_AUTH_REGISTER_COUNT", 20),
        help="Sequential POST /auth/register calls (env STRESS_AUTH_REGISTER_COUNT)",
    )
    p.add_argument(
        "--allow-errors",
        action="store_true",
        help="Always exit 0 if server reachable, even when some requests fail (overrides STRESS_FAIL_ON_ERROR)",
    )
    return p.parse_args(argv)


@dataclass
class Result:
    endpoint: str
    method: str
    total_requests: int
    success_count: int
    error_count: int
    latencies_ms: list[float]
    duration_sec: float
    skipped: bool = False
    note: str = ""

    @property
    def throughput_rps(self) -> float:
        return self.total_requests / self.duration_sec if self.duration_sec > 0 else 0

    @property
    def error_rate_pct(self) -> float:
        return 100 * self.error_count / self.total_requests if self.total_requests > 0 else 0

    def percentiles(self) -> dict[str, float]:
        s = sorted(self.latencies_ms) if self.latencies_ms else []
        n = len(s)
        return {
            "p50": s[int(n * 0.50)] if n else 0,
            "p90": s[int(n * 0.90)] if n else 0,
            "p95": s[int(n * 0.95)] if n else 0,
            "p99": s[int(n * 0.99)] if n else 0,
            "min": min(s) if s else 0,
            "max": max(s) if s else 0,
            "mean": statistics.mean(s) if s else 0,
        }


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, float]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (time.perf_counter() - t0) * 1000
    except Exception:
        return -1, (time.perf_counter() - t0) * 1000


def _http_post(url: str, data: bytes | dict, headers: dict | None = None, timeout: int = 30) -> tuple[int, float]:
    h = dict(headers or {})
    if isinstance(data, dict):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    else:
        body = data
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (time.perf_counter() - t0) * 1000
    except Exception:
        return -1, (time.perf_counter() - t0) * 1000


def probe_server(base_url: str) -> tuple[bool, str]:
    status, _ = _http_get(f"{base_url}/health", timeout=5)
    if status == 200:
        return True, "ok"
    if status == -1:
        return False, f"Cannot connect to {base_url}. Start: uvicorn my_agent_os.api_gateway.main:app --host 0.0.0.0 --port 8000"
    return False, f"/health returned HTTP {status}"


def _worker_batch_get(url: str, headers: dict | None, count: int) -> tuple[int, int, list[float]]:
    ok = err = 0
    latencies: list[float] = []
    for _ in range(count):
        status, ms = _http_get(url, headers)
        if 200 <= status < 300:
            ok += 1
            latencies.append(ms)
        else:
            err += 1
    return ok, err, latencies


def _worker_batch_post(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    count: int,
) -> tuple[int, int, list[float]]:
    ok = err = 0
    latencies: list[float] = []
    for _ in range(count):
        status, ms = _http_post(url, body, headers, timeout=30)
        if 200 <= status < 300:
            ok += 1
            latencies.append(ms)
        else:
            err += 1
    return ok, err, latencies


def run_bounded_get(
    name: str,
    url: str,
    total: int,
    workers: int,
    headers: dict | None = None,
) -> Result:
    per_worker = max(1, total // workers)
    actual_total = per_worker * workers
    start = time.perf_counter()
    all_lat: list[float] = []
    success = errors = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker_batch_get, url, headers, per_worker) for _ in range(workers)]
        for f in as_completed(futs):
            o, e, lat = f.result()
            success += o
            errors += e
            all_lat.extend(lat)

    elapsed = time.perf_counter() - start
    return Result(name, "GET", actual_total, success, errors, all_lat, elapsed)


def run_bounded_post_json(
    name: str,
    url: str,
    body: dict[str, Any],
    total: int,
    workers: int,
    headers: dict[str, str],
) -> Result:
    per_worker = max(1, total // workers)
    actual_total = per_worker * workers
    start = time.perf_counter()
    all_lat: list[float] = []
    success = errors = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [
            ex.submit(_worker_batch_post, url, body, headers, per_worker) for _ in range(workers)
        ]
        for f in as_completed(futs):
            o, e, lat = f.result()
            success += o
            errors += e
            all_lat.extend(lat)

    elapsed = time.perf_counter() - start
    return Result(name, "POST", actual_total, success, errors, all_lat, elapsed)


def stress_auth_register(base_url: str, n: int) -> Result:
    latencies: list[float] = []
    success = errors = 0
    start = time.perf_counter()
    for _ in range(n):
        status, ms = _http_post(
            f"{base_url}/auth/register",
            {
                "email": f"stress-{uuid.uuid4().hex[:12]}@stress.local",
                "password": "StressTest123!",
                "plan": "free",
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        latencies.append(ms)
        if 200 <= status < 300:
            success += 1
        else:
            errors += 1
    elapsed = time.perf_counter() - start
    return Result("POST /auth/register", "POST", n, success, errors, latencies, elapsed)


def format_result(r: Result) -> str:
    if r.skipped or r.total_requests == 0:
        return f"  {r.endpoint}: SKIPPED — {r.note or 'no requests'}"
    p = r.percentiles()
    return (
        f"  {r.endpoint}\n"
        f"    Requests: {r.total_requests} | Success: {r.success_count} | Errors: {r.error_count} | "
        f"Error rate: {r.error_rate_pct:.1f}%\n"
        f"    Throughput: {r.throughput_rps:.1f} req/s\n"
        f"    Latency (ms): min={p['min']:.2f} p50={p['p50']:.2f} p95={p['p95']:.2f} p99={p['p99']:.2f} max={p['max']:.2f}"
    )


def _save_report(report: dict[str, Any], results: list[Result]) -> None:
    json_path = RESULTS_DIR / "stress_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    md_lines = [
        "# Agent OS — Stress Test Report",
        "",
        f"**Generated:** {report.get('timestamp', '')}",
    ]
    if "error" in report:
        md_lines += ["", "## Error", "", str(report["error"]), ""]
        md_lines.append("*Fix: start the API then re-run `stress/run.sh`.*")
    else:
        cfg = report.get("config", {})
        md_lines += [
            "",
            f"**Config:** {cfg.get('base_url')} | Requests/phase: {cfg.get('total_requests_per_phase')} | Workers: {cfg.get('workers')}",
            "",
            "## Summary",
            "",
            "| Endpoint | Total | Success | Errors | Error % | RPS | P50 (ms) | P95 (ms) | P99 (ms) |",
            "|----------|-------|---------|--------|---------|-----|----------|----------|----------|",
        ]
        skipped_notes: list[str] = []
        for rep in report["results"]:
            if rep.get("skipped"):
                skipped_notes.append(f"- **{rep['endpoint']}**: {rep.get('note', 'skipped')}")
                continue
            l = rep["latency_ms"]
            md_lines.append(
                f"| {rep['endpoint']} | {rep['total']} | {rep['success']} | {rep['errors']} | "
                f"{rep['error_rate_pct']}% | {rep['throughput_rps']} | {l['p50']:.2f} | {l['p95']:.2f} | {l['p99']:.2f} |"
            )
        if skipped_notes:
            md_lines += ["", "### Skipped (no load applied)", ""] + skipped_notes
        md_lines += [
            "",
            "## Interpretation",
            "",
            "- **GET/POST parallel phases**: fixed total requests split across worker threads (bounded).",
            "- **POST /auth/register**: unique emails per call; exercises SQLite user store.",
            "- **Memory & extended health**: require `API_KEY_OWNER` matching server `my_agent_os/config/.env`.",
        ]

    with open(RESULTS_DIR / "stress_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    base_url = args.base_url.rstrip("/")
    total = args.requests
    workers = max(1, args.workers)
    register_n = max(0, args.register_count)
    api_key = os.getenv("API_KEY_OWNER", "").strip()
    fail_on_error = not args.allow_errors and os.getenv("STRESS_FAIL_ON_ERROR", "1").lower() not in (
        "0",
        "false",
        "no",
    )

    hdr_key: dict[str, str] = {"X-API-Key": api_key} if api_key else {}

    print("Agent OS — Stress Test")
    print(f"  BASE_URL={base_url}  REQUESTS/phase={total}  WORKERS={workers}")
    print(f"  REGISTER_COUNT={register_n}")
    print(f"  API_KEY_OWNER={'set' if api_key else 'not set (memory + extended health skipped)'}")
    print(f"  RESULTS_DIR={RESULTS_DIR}")
    print(f"  FAIL_ON_ERROR={fail_on_error}")
    print()

    ok, msg = probe_server(base_url)
    if not ok:
        print("ERROR:", msg)
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": msg,
            "config": {"base_url": base_url},
            "results": [],
        }
        _save_report(report, [])
        return 1

    print("Server OK — running load phases…\n")

    results: list[Result] = []
    phases_get: list[tuple[str, str, dict | None]] = [
        ("GET /health", f"{base_url}/health", None),
        ("GET /billing/plans", f"{base_url}/billing/plans", None),
        ("GET /", f"{base_url}/", None),
        ("GET /setup", f"{base_url}/setup", None),
    ]
    for name, url, hdr in phases_get:
        r = run_bounded_get(name, url, total, workers, hdr)
        results.append(r)
        print(format_result(r))

    if api_key:
        auth_hdr = {**hdr_key, "Content-Type": "application/json"}
        extra: list[tuple[str, str, dict | None] | tuple[str, str, dict, str]] = [
            ("GET /health/extended", f"{base_url}/health/extended", hdr_key),
            ("GET /memory/stats", f"{base_url}/memory/stats", hdr_key),
            ("GET /memory/list", f"{base_url}/memory/list?limit=50", hdr_key),
            ("GET /memory/sessions", f"{base_url}/memory/sessions?limit=20", hdr_key),
        ]
        for item in extra:
            name, url, hdr = item[0], item[1], item[2]
            r = run_bounded_get(name, url, total, workers, hdr)
            results.append(r)
            print(format_result(r))

        r = run_bounded_post_json(
            "POST /memory/search",
            f"{base_url}/memory/search",
            {"query": "stress", "top_k": 10},
            total,
            workers,
            auth_hdr,
        )
        results.append(r)
        print(format_result(r))
    else:
        for label, note in (
            ("GET /health/extended", "API_KEY_OWNER not set"),
            ("GET /memory/stats", "API_KEY_OWNER not set"),
            ("GET /memory/list", "API_KEY_OWNER not set"),
            ("GET /memory/sessions", "API_KEY_OWNER not set"),
            ("POST /memory/search", "API_KEY_OWNER not set"),
        ):
            results.append(Result(label, "GET", 0, 0, 0, [], 0, skipped=True, note=note))
            print(format_result(results[-1]))

    if register_n > 0:
        r = stress_auth_register(base_url, register_n)
        results.append(r)
        print(format_result(r))

    report: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "base_url": base_url,
            "total_requests_per_phase": total,
            "workers": workers,
            "register_count": register_n,
            "api_key_set": bool(api_key),
        },
        "results": [],
    }
    for r in results:
        if r.skipped or r.total_requests == 0:
            report["results"].append(
                {"endpoint": r.endpoint, "skipped": True, "note": r.note or "skipped"},
            )
            continue
        report["results"].append(
            {
                "endpoint": r.endpoint,
                "method": r.method,
                "total": r.total_requests,
                "success": r.success_count,
                "errors": r.error_count,
                "error_rate_pct": round(r.error_rate_pct, 2),
                "throughput_rps": round(r.throughput_rps, 2),
                "latency_ms": r.percentiles(),
                "duration_sec": round(r.duration_sec, 3),
            },
        )

    _save_report(report, results)
    print(f"\nSaved: {RESULTS_DIR / 'stress_results.json'} and stress_report.md")

    if fail_on_error:
        for r in results:
            if not r.skipped and r.total_requests > 0 and r.error_count > 0:
                print("\nFAIL: one or more phases reported errors. Set STRESS_FAIL_ON_ERROR=0 to ignore.", file=sys.stderr)
                return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
