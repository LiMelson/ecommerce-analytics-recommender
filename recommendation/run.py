"""Run the real handover audit or offline benchmark from the repository root."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from data_algo.data_algo.path_utils import atomic_output

from .benchmark import History, HistoryBands, evaluate, nearest_rank, split_events, time_boundaries, window_summary
from .popular import PopularModel, local_dataset_id
from .sample import audit_sample, read_sample

HERE = Path(__file__).resolve().parent


def _code_sha256(config_path):
    digest = hashlib.sha256()
    for path in [*sorted(HERE.glob("*.py")), HERE / "requirements.txt", config_path]:
        digest.update(path.name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def _peak_rss_mib():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t),
                ("paged_pool", ctypes.c_size_t),
                ("peak_nonpaged_pool", ctypes.c_size_t),
                ("nonpaged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        get_memory = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        get_memory.restype = wintypes.BOOL
        info = MemoryCounters()
        info.cb = ctypes.sizeof(info)
        if get_memory(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            return info.peak_working_set / 1024**2
        return None

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024**2 if platform.system() == "Darwin" else 1024)


def _environment():
    revision = None
    if (HERE.parent / ".git").exists() and shutil.which("git"):
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
        ).stdout.strip()
    return {
        "git_head": revision or None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "dependencies": {name: version(name) for name in ("pandas", "numpy", "scipy", "pyarrow", "duckdb", "pytest")},
    }


def _evaluate_stage(name, window, model, bands, k, initial_train):
    source_counts = Counter()
    latency_ms = []
    rankings_digest = hashlib.sha256()

    def rank(user_id, limit):
        start = perf_counter()
        ranking = model.recommend(user_id, limit)
        latency_ms.append((perf_counter() - start) * 1000)
        source_counts[ranking.source] += 1
        item_ids = tuple(item.item_id for item in ranking.items)
        line = json.dumps((user_id, ranking.source, item_ids), ensure_ascii=False, separators=(",", ":"))
        rankings_digest.update(line.encode("utf-8") + b"\n")
        return item_ids

    start = perf_counter()
    scores = evaluate(window, model.history, bands, rank, k)
    seconds = perf_counter() - start
    latency_ms.sort()
    summary = {
        "model_metadata": vars(model.metadata),
        "users_known_in_initial_train": len(set(window["user_id"]) & initial_train.event_counts.keys()),
        "recommendation_source_users": dict(source_counts),
        "rankings_sha256": rankings_digest.hexdigest(),
        **scores,
    }
    runtime = {
        f"{name}_evaluation_seconds": seconds,
        f"{name}_recommend_ms": {
            "requests": len(latency_ms),
            "p50": nearest_rank(latency_ms, 0.5) if latency_ms else None,
            "p95": nearest_rank(latency_ms, 0.95) if latency_ms else None,
        },
    }
    return summary, runtime


def run_benchmark(events, config, digest, code_sha256):
    boundaries = time_boundaries(config["observation_start"], config["observation_end_exclusive"])
    train, validation, test = split_events(events, boundaries)
    bands = HistoryBands.fit(train)
    metadata = {
        "dataset_id": local_dataset_id(digest),
        "sample_sha256": digest,
        "model_version": config["model_version"],
        "protocol_version": config["protocol_version"],
        "code_sha256": code_sha256,
    }
    start = perf_counter()
    validation_metadata = {**metadata, "model_version": config["validation_model_version"]}
    validation_model = PopularModel.fit(train, boundaries[1], **validation_metadata)
    validation_build = perf_counter() - start

    # Freeze configuration and training-derived bands before evaluating the test window.
    initial_train = validation_model.history
    validation_results, validation_runtime = _evaluate_stage(
        "validation", validation, validation_model, bands, config["top_k"], initial_train
    )
    del validation_model

    start = perf_counter()
    refit_events = events.loc[events["event_time"].lt(boundaries[2])]
    test_model = PopularModel.fit(refit_events, boundaries[2], **metadata)
    test_build = perf_counter() - start
    test_results, test_runtime = _evaluate_stage("test", test, test_model, bands, config["top_k"], initial_train)

    empty = History.fit(train.iloc[:0], boundaries[0])
    history_counts = sorted(initial_train.event_counts.values())
    return {
        "config": config,
        **metadata,
        "rules": {
            "candidate_catalog": "all_items_observed_before_cutoff",
            "block_pre_cutoff_purchases": True,
            "negative_sampling": False,
            "weights": {"buy": 1, "all_other_events": 0},
            "sort": ["purchase_count_desc", "item_id_string_asc"],
            "ranking_fallback": "same_popularity_order_for_users_without_history",
            "random_seed": None,
            "validation_fitted_on": ["train"],
            "test_fitted_on": ["train", "validation"],
            "band_thresholds_fitted_on": ["train"],
        },
        "history_bands": {
            **vars(bands),
            "initial_train_users": len(history_counts),
            "initial_train_events_per_user_quantiles": {
                f"p{q}": nearest_rank(history_counts, q / 100) for q in (25, 50, 75, 90, 95, 100)
            },
        },
        "windows": {
            "train": window_summary(train, boundaries[0], boundaries[1], initial_train, empty),
            "validation": window_summary(validation, boundaries[1], boundaries[2], initial_train, initial_train),
            "test": window_summary(test, boundaries[2], boundaries[3], initial_train, test_model.history),
        },
        "validation": validation_results,
        "test": test_results,
    }, {
        "validation_build_seconds": validation_build,
        "test_refit_seconds": test_build,
        **validation_runtime,
        **test_runtime,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("audit", "benchmark"))
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "popular_v1.json")
    parser.add_argument("--out-dir", type=Path, default=HERE.parent / "artifacts" / "p0-rec-01")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    code_sha256 = _code_sha256(args.config)
    start = perf_counter()
    events, storage, digest = read_sample(args.sample, config)
    read_seconds = perf_counter() - start
    if args.stage == "audit":
        audit_start = perf_counter()
        result = audit_sample(events, storage, digest, config)
        timings = {"audit_seconds": perf_counter() - audit_start}
    else:
        result, timings = run_benchmark(events, config, digest, code_sha256)
    payload = {
        "result": result,
        "provenance": {"code_sha256": code_sha256, **_environment()},
        "runtime": {
            "read_and_validate_seconds": read_seconds,
            **timings,
            "total_seconds_before_output": perf_counter() - start,
            "process_peak_rss_mib": _peak_rss_mib(),
        },
    }
    output = args.out_dir / f"{args.stage}.json"
    with atomic_output(output) as temporary:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(output.resolve())
    if args.stage == "audit":
        print(f"rows={result['rows']} users={result['users']} purchase_users={result['purchase_users']}")
    else:
        for stage in ("validation", "test"):
            metrics = result[stage]["groups"]["overall"]["primary"]
            print(f"{stage}: {metrics}")


if __name__ == "__main__":
    main()
