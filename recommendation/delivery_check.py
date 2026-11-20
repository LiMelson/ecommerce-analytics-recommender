"""Aggregate real-asset regression and fresh-process Python inference measurements."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

from data_algo.data_algo.path_utils import atomic_output

from .delivery import build_assets
from .engine import RecommendationEngine
from .popular import PopularModel
from .run import _peak_rss_mib
from .sample import read_sample, sha256_file


def current_rss_mib():
    if os.name != "nt":
        return None
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

    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.working / 1024**2


def benchmark_worker(registry, version):
    before = current_rss_mib()
    start = perf_counter()
    engine = RecommendationEngine.from_registry(registry, preload=False)
    engine.load(version)
    load_seconds = perf_counter() - start
    after = current_rss_mib()
    model = engine._models[version]
    historical_users = sorted(model.history.event_counts)
    users = ["local-cold-start-probe"] + historical_users[:999]
    users = (users * 1000)[:1000]
    request = {"dataset_id": model.metadata.dataset_id, "model_version": version, "limit": 10}
    started = perf_counter()
    engine.predict(user_id=users[0], **request)
    single_ms = (perf_counter() - started) * 1000
    latencies = []
    empty, errors = 0, 0
    for user in users:
        started = perf_counter()
        try:
            response = engine.predict(user_id=user, **request)
            empty += not response["items"]
        except ValueError:
            errors += 1
        latencies.append((perf_counter() - started) * 1000)
    batch_times = {}
    for size in (100, 1000):
        started = perf_counter()
        responses = engine.predict_batch(users[:size], **request)
        batch_times[str(size)] = {
            "seconds": perf_counter() - started,
            "empty_results": sum(not result["items"] for result in responses),
        }
    latencies.sort()
    registration = engine._registrations[version]
    return {
        "model_version": version,
        "file_bytes": Path(registration["path"]).stat().st_size,
        "load_seconds_including_file_verification": load_seconds,
        "rss_before_load_mib": before,
        "rss_after_load_mib": after,
        "rss_load_delta_mib": after - before if after is not None and before is not None else None,
        "process_peak_rss_mib": _peak_rss_mib(),
        "single_cold_request_ms": single_ms,
        "latency_1000_requests_ms": {"p50": latencies[499], "p95": latencies[949]},
        "batch": batch_times,
        "errors": errors,
        "empty_results": empty,
        "workload": "one_new_user_then_999_lexically_first_fitted_users_cycled_if_needed",
        "serial_python_inference": True,
    }


def original_fingerprint(model, users):
    digest = hashlib.sha256()
    for user in users:
        ranking = model.recommend(user, 10)
        trace = (user, ranking.source, [item.item_id for item in ranking.items])
        if not isinstance(model, PopularModel):
            trace += ([item.source for item in ranking.items],)
        digest.update(json.dumps(trace, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def representative_users(fit, models):
    behavior_sets = fit.groupby("user_id")["behavior_type"].agg(frozenset)
    histories = models[0][1].history

    def first(matches):
        return next(iter(sorted(matches)), None)

    purchase = fit[fit["behavior_type"].eq("buy")]
    repeats = purchase.groupby("user_id").size() > purchase.groupby("user_id")["item_id"].nunique()
    examples = {
        "new_user": "local-cold-start-probe",
        "only_view": first(user for user, kinds in behavior_sets.items() if kinds == frozenset({"pv"})),
        "cart_without_buy": first(
            user for user, kinds in behavior_sets.items() if "cart" in kinds and "buy" not in kinds
        ),
        "purchase_history": first(histories.purchased),
        "high_activity": min(histories.event_counts, key=lambda user: (-histories.event_counts[user], user)),
        "repeat_purchase_history": first(repeats[repeats].index),
    }
    itemcf = next(model for kind, model, _, _ in models if kind == "itemcf")
    no_neighbors = None
    for user, row in sorted(itemcf._user_rows.items()):
        left, right = itemcf._interactions.indptr[row : row + 2]
        seeds = itemcf._interactions.indices[left:right]
        if all(itemcf._neighbors.indptr[seed] == itemcf._neighbors.indptr[seed + 1] for seed in seeds):
            no_neighbors = user
            break
    examples["no_similar_neighbors"] = no_neighbors
    return examples


def verify_real(sample, out_dir):
    models, build_report = build_assets(sample, out_dir)
    original = RecommendationEngine.from_models([model for _, model, _, _ in models])
    loaded = RecommendationEngine.from_registry(out_dir / "registry.json")
    protocol_path = Path(__file__).parent / "popular_v1.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    events, _, _ = read_sample(sample, protocol)
    cutoff = models[0][1].history.cutoff
    fit = events[events["event_time"] < cutoff]
    examples = representative_users(fit, models)
    representative = {}
    for label, user in examples.items():
        representative[label] = {"available": user is not None, "models": {}}
        if user is None:
            continue
        for kind, model, _, _ in models:
            args = {"dataset_id": model.metadata.dataset_id, "model_version": model.metadata.model_version, "limit": 10}
            expected, trace = original.predict_with_trace(user_id=user, **args)
            assert expected == loaded.predict(user_id=user, **args)
            assert loaded.predict_batch([user, "local-cold-start-probe", user], **args)[::2] == [expected, expected]
            representative[label]["models"][kind] = {
                "items": len(expected["items"]),
                "source": trace["source"],
                "item_sources": dict(Counter(trace["item_sources"])),
                "roundtrip_continuous_and_batch_equal": True,
            }

    test_users = sorted(events.loc[events["event_time"].ge(cutoff), "user_id"].unique())
    root = Path(__file__).parent / "reports"
    references = {
        "popular": json.loads((root / "p0-rec-01-benchmark.json").read_text(encoding="utf-8"))["result"]["test"],
        "itemcf": json.loads((root / "p0-rec-02-test.json").read_text(encoding="utf-8"))["trial"]["evaluation"],
        "bpr": json.loads((root / "p0-rec-03-test.json").read_text(encoding="utf-8"))["trial"]["evaluation"],
    }
    regression = {}
    for kind, model, _, _ in models:
        args = {"dataset_id": model.metadata.dataset_id, "model_version": model.metadata.model_version}
        for start in range(0, len(test_users), 100):
            users = test_users[start : start + 100]
            assert loaded.predict_batch(users, **args) == original.predict_batch(users, **args)
        fingerprint = original_fingerprint(model, test_users)
        assert fingerprint == references[kind]["rankings_sha256"]
        regression[kind] = {
            "requests": len(test_users),
            "frozen_rankings_sha256": fingerprint,
            "memory_loaded_and_batch_equal": True,
        }
    timings = {}
    for _, model, _, _ in models:
        command = [
            sys.executable,
            "-m",
            "recommendation.delivery_check",
            "worker",
            "--registry",
            str(out_dir / "registry.json"),
            "--model-version",
            model.metadata.model_version,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        timings[model.metadata.model_version] = json.loads(result.stdout)
    return {
        "build": build_report,
        "representative_user_types": representative,
        "full_frozen_request_regression": regression,
        "performance": timings,
        "historical_report_sha256": {
            name: sha256_file(root / name)
            for name in (
                "p0-rec-01-benchmark.json",
                "p0-rec-02-validation.json",
                "p0-rec-02-test.json",
                "p0-rec-03-validation.json",
                "p0-rec-03-test.json",
            )
        },
        "test_metrics_recomputed": False,
        "test_parameters_reselected": False,
        "measurement_scope": "CPU_serial_Python_memory_inference_no_HTTP",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--registry", type=Path, required=True)
    worker.add_argument("--model-version", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--sample", type=Path, required=True)
    verify.add_argument("--out-dir", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(benchmark_worker(args.registry, args.model_version), indent=2))
    else:
        result = verify_real(args.sample, args.out_dir)
        with atomic_output(args.report) as temporary:
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(args.report.resolve())


if __name__ == "__main__":
    main()
