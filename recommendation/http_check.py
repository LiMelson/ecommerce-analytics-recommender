"""Measure localhost HTTP with existing assets and compare frozen rankings without scoring labels."""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from random import Random
from time import perf_counter, sleep

import httpx

from .benchmark import nearest_rank
from .delivery_check import original_fingerprint, representative_users
from .engine import RecommendationEngine
from .http_service import DEFAULT_REGISTRY, MAX_RESPONSE_BYTES
from .run import HERE, ROOT, _environment
from .sample import read_sample, sha256_file

CONCURRENCY = 8
SEQ_REQUESTS = 120
CONCURRENT_REQUESTS = 240
TIMEOUT_SECONDS = 5
DEMO_USER = "local-http-demo-04b"


@contextmanager
def local_server(registry, log_path):
    command = [sys.executable, "-m", "recommendation.http_service", "--registry", str(registry), "--port", "0"]
    with log_path.open("w", encoding="utf-8") as log:
        started = perf_counter()
        with subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            try:
                for _ in range(400):
                    lines = log_path.read_text(encoding="utf-8", errors="replace")
                    match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", lines)
                    server = re.search(r"Started server process \[(\d+)\]", lines)
                    status = process.poll()
                    if status is not None:
                        raise RuntimeError(f"Local service exited with {status}; see {log_path}")
                    if match and server:
                        url = f"http://127.0.0.1:{match.group(1)}"
                        with httpx.Client(timeout=TIMEOUT_SECONDS, trust_env=False) as client:
                            ready = client.get(f"{url}/ready").raise_for_status().json()
                        yield (
                            url,
                            # A Windows venv launcher can be the parent process.
                            int(server.group(1)),
                            {
                                "spawn_to_ready_seconds": perf_counter() - started,
                                "model_load_seconds": ready["model_load_seconds"],
                                "versions": ready["model_versions"],
                                "loopback_port": int(match.group(1)),
                            },
                        )
                        return
                    sleep(0.05)
                raise RuntimeError(f"Local service did not become ready; see {log_path}")
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def process_memory_mib(pid):
    if os.name != "nt":
        try:
            contents = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            return None
        metrics = {key: int(value) / 1024 for key, value in re.findall(r"^(VmRSS|VmHWM):\s+(\d+) kB", contents, re.M)}
        return {"working_set": metrics.get("VmRSS"), "peak_working_set": metrics.get("VmHWM")}
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("faults", wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "peak",
                    "working",
                    "peak_pool",
                    "pool",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ],
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        get_memory = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        get_memory.restype = wintypes.BOOL
        counts = MemoryCounters()
        counts.cb = ctypes.sizeof(counts)
        if not get_memory(handle, ctypes.byref(counts), counts.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"working_set": counts.working / 1024**2, "peak_working_set": counts.peak / 1024**2}
    finally:
        kernel.CloseHandle(handle)


def measured_request(client, case):
    request, expected = case
    started = perf_counter()
    try:
        response = client.post("/predict", json=request)
        elapsed = perf_counter() - started
        size = len(response.content)
        if response.status_code != 200:
            return elapsed, size, f"http_{response.status_code}"
        if size > MAX_RESPONSE_BYTES or elapsed > TIMEOUT_SECONDS:
            return elapsed, size, "exceeds_contract_limit"
        if response.json() != expected:
            return elapsed, size, "prediction_mismatch"
        return elapsed, size, None
    except (httpx.HTTPError, ValueError) as error:
        return perf_counter() - started, 0, type(error).__name__


def measured_workload(url, cases, count, concurrency):
    scheduled = [cases[index % len(cases)] for index in range(count)]
    with httpx.Client(
        base_url=url,
        timeout=TIMEOUT_SECONDS,
        trust_env=False,
        limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
    ) as client:
        started = perf_counter()
        if concurrency == 1:
            measurements = [measured_request(client, case) for case in scheduled]
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                measurements = list(pool.map(lambda case: measured_request(client, case), scheduled))
        seconds = perf_counter() - started
    latency = sorted(elapsed * 1000 for elapsed, _, _ in measurements)
    errors = Counter(error for _, _, error in measurements if error)
    return {
        "requests": count,
        "concurrency": concurrency,
        "seconds": seconds,
        "requests_per_second": count / seconds,
        "errors": dict(errors),
        "error_rate": sum(errors.values()) / count,
        "p50_ms": nearest_rank(latency, 0.50),
        "p95_ms": nearest_rank(latency, 0.95),
        "max_ms": latency[-1],
        "max_response_bytes": max(size for _, size, _ in measurements),
    }


def file_state(paths):
    return {path.name: {"sha256": sha256_file(path), "mtime_ns": path.stat().st_mtime_ns} for path in paths}


def verify_http(registry, sample, out_dir):
    if not __debug__:
        raise RuntimeError("Run HTTP verification without Python optimization so all checks execute")
    registry = registry.resolve()
    directory = out_dir.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a fresh output directory to preserve previous HTTP test evidence")
    directory.mkdir(parents=True, exist_ok=True)
    registrations = json.loads(registry.read_text(encoding="utf-8"))["models"]
    engine = RecommendationEngine.from_registry(registry)
    tracked_files = [registry] + [(registry.parent / entry["file"]).resolve() for entry in registrations.values()]
    before = file_state(tracked_files)
    protocol = json.loads((HERE / "popular_v1.json").read_text(encoding="utf-8"))
    events, _, sample_sha256 = read_sample(sample, protocol)
    models = [(entry["kind"], engine._models[name], None, None) for name, entry in registrations.items()]
    cutoff = models[0][1].history.cutoff
    fit = events[events["event_time"] < cutoff]
    examples = representative_users(fit, models)
    examples["new_user"] = DEMO_USER
    assert all(user is not None for user in examples.values())
    assert DEMO_USER not in models[0][1].history.event_counts
    frozen_users = sorted(events.loc[events["event_time"].ge(cutoff), "user_id"].unique())
    baseline = json.loads((HERE / "reports/p0-rec-04a-verification.json").read_text(encoding="utf-8"))
    frozen_regression = {}
    cases = {}
    rng = Random(20261120)
    fitted = sorted(models[0][1].history.event_counts)
    users = list(examples.values()) + rng.sample(fitted, min(33, len(fitted)))
    for kind, model, _, _ in models:
        assert model.metadata.sample_sha256 == sample_sha256 and model.history.cutoff == cutoff
        assert model.metadata.dataset_id.startswith("local-parquet-sha256-")
        fingerprint = original_fingerprint(model, frozen_users)
        assert fingerprint == baseline["full_frozen_request_regression"][kind]["frozen_rankings_sha256"]
        frozen_regression[kind] = {"requests": len(frozen_users), "rankings_sha256": fingerprint}
        requests = [
            {
                "user_id": user,
                "dataset_id": model.metadata.dataset_id,
                "model_version": model.metadata.model_version,
                "limit": 10,
            }
            for user in users
        ]
        cases[kind] = [(request, engine.predict(**request)) for request in requests]

    examples_out, summary, errors = {}, {}, {}
    with local_server(registry, directory / "server.log") as (url, pid, startup):
        assert set(startup["versions"]) == set(registrations)
        ready_memory = process_memory_mib(pid)
        with httpx.Client(base_url=url, timeout=TIMEOUT_SECONDS, trust_env=False) as client:
            assert client.get("/health").json()["status"] == "ready"
            for kind, model_cases in cases.items():
                request, expected = model_cases[0]
                started = perf_counter()
                response = client.post("/predict", json=request)
                elapsed = perf_counter() - started
                assert response.status_code == 200 and response.json() == expected
                examples_out[kind] = {"request": request, "response": response.json()}
                summary[kind] = {"single_cold_http_ms": elapsed * 1000, "single_response_bytes": len(response.content)}
            request = cases["itemcf"][0][0]
            for label, changes, status, code in (
                ("unknown", {"model_version": "bpr_v1"}, 404, "unknown_model"),
                ("dataset", {"dataset_id": "unbound-local-test"}, 409, "dataset_mismatch"),
                ("invalid_limit", {"limit": 11}, 422, "invalid_limit"),
            ):
                response = client.post("/predict", json={**request, **changes})
                assert response.status_code == status and response.json()["error"]["code"] == code
                errors[label] = {"status": status, "error": response.json()["error"]}
        for kind, model_cases in cases.items():
            summary[kind]["serial"] = measured_workload(url, model_cases, SEQ_REQUESTS, 1)
            summary[kind]["concurrent"] = measured_workload(url, model_cases, CONCURRENT_REQUESTS, CONCURRENCY)
        mixed_cases = [case for group in zip(*cases.values(), strict=True) for case in group]
        mixed = measured_workload(url, mixed_cases, CONCURRENT_REQUESTS, CONCURRENCY)
        final_memory = process_memory_mib(pid)
    assert file_state(tracked_files) == before
    workload_results = [mixed] + [r[mode] for r in summary.values() for mode in ("serial", "concurrent")]
    report = {
        "environment": {
            **_environment(),
            **{p: version(p) for p in ("fastapi", "uvicorn", "httpx", "starlette", "pydantic")},
        },
        "measurement_scope": "real_HTTP_loopback_Uvicorn_single_process_existing_04A_assets",
        "client_timeout_seconds": TIMEOUT_SECONDS,
        "response_limit_bytes": MAX_RESPONSE_BYTES,
        "workload": {
            "representative_user_types": list(examples),
            "seeded_fitted_users": len(users) - len(examples),
            "client_connection_reuse": True,
            "request_timing_includes_http_response_body": True,
        },
        "asset_sha256": {name: row["sha256"] for name, row in before.items()},
        "asset_hash_and_mtime_unchanged": True,
        "sample_sha256": sample_sha256,
        "startup": startup,
        "server_memory_mib": {
            "process_identity": "uvicorn_server_pid_from_startup_log",
            "at_readiness": ready_memory,
            "after_requests": final_memory,
        },
        "models": summary,
        "mixed_concurrent": mixed,
        "intentional_error_checks": errors,
        "frozen_python_request_regression": frozen_regression,
        "test_labels_scored": False,
        "passed": all(r["error_rate"] == 0 for r in workload_results),
    }
    (directory / "http-examples.local.json").write_text(
        json.dumps(examples_out, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (directory / "http-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(directory / "http-verification.json")
    if not report["passed"]:
        raise SystemExit("HTTP verification failed. Aggregate errors were saved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    verify_http(args.registry, args.sample, args.out_dir)


if __name__ == "__main__":
    main()
