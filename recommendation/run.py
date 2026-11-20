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

from .benchmark import (
    BenchmarkStage,
    History,
    HistoryBands,
    evaluate,
    nearest_rank,
    split_events,
    time_boundaries,
    window_summary,
)
from .popular import PopularModel, local_dataset_id
from .sample import audit_sample, read_sample

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def source_fingerprints(*configs):
    pipeline = ROOT / "data_algo"
    files = [
        *sorted(HERE.glob("*.py")),
        HERE / "requirements.txt",
        pipeline / "requirements.txt",
        *(
            pipeline / "data_algo" / name
            for name in ("__init__.py", "schema.py", "io_utils.py", "path_utils.py", "website_export.py")
        ),
        ROOT / "docs" / "P0-REC-01-Benchmark.md",
    ]
    paths = {path.relative_to(ROOT).as_posix(): path for path in files}
    paths.update({f"config/{path.name}": path for path in configs})
    return {
        key: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for key, path in sorted(paths.items())
    }


def _code_sha256(*configs):
    fingerprints = json.dumps(source_fingerprints(*configs), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(fingerprints.encode()).hexdigest()


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
    git = {}
    if (HERE.parent / ".git").exists() and shutil.which("git"):
        for name, args in (
            ("git_head", ["rev-parse", "HEAD"]),
            ("git_branch", ["branch", "--show-current"]),
            ("git_worktree_dirty", ["status", "--porcelain"]),
        ):
            stdout = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
            git[name] = bool(stdout) if name == "git_worktree_dirty" else stdout or None
    return {
        **git,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "dependencies": {name: version(name) for name in ("pandas", "numpy", "scipy", "pyarrow", "duckdb", "pytest")},
    }


def _evaluate_stage(name, stage, model, bands, k, initial_train, *, track_item_sources=False):
    source_counts = Counter()
    item_sources = Counter()
    item_sources_by_band = {name: Counter() for name in ("cold", "low", "medium", "high")}
    latency_ms = []
    rankings_digest = hashlib.sha256()

    def rank(user_id, limit):
        start = perf_counter()
        ranking = model.recommend(user_id, limit)
        latency_ms.append((perf_counter() - start) * 1000)
        source_counts[ranking.source] += 1
        item_ids = tuple(item.item_id for item in ranking.items)
        trace = (user_id, ranking.source, item_ids)
        if track_item_sources:
            sources = [item.source for item in ranking.items]
            item_sources.update(sources)
            band = bands.segment(model.history.event_counts.get(user_id, 0))
            item_sources_by_band[band].update(sources)
            trace += (sources,)
        line = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
        rankings_digest.update(line.encode("utf-8") + b"\n")
        return item_ids

    start = perf_counter()
    scores = evaluate(stage, model.history, bands, rank, k)
    seconds = perf_counter() - start
    latency_ms.sort()
    summary = {
        "model_metadata": vars(model.metadata),
        "users_known_in_initial_train": len(set(stage.target_events["user_id"]) & initial_train.event_counts.keys()),
        "recommendation_source_users": dict(source_counts),
        "rankings_sha256": rankings_digest.hexdigest(),
        **scores,
    }
    if track_item_sources:
        summary["recommendation_item_sources"] = dict(item_sources)
        summary["recommendation_item_sources_by_band"] = {
            band: dict(counts) for band, counts in item_sources_by_band.items()
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
    split = split_events(events, boundaries)
    train, validation, test = split
    validation_stage = BenchmarkStage.from_split(split, boundaries, "validation")
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
    validation_model = PopularModel.fit(train, boundaries[1], fit_start=boundaries[0], **validation_metadata)
    validation_build = perf_counter() - start

    # Freeze configuration and training-derived bands before evaluating the test window.
    initial_train = validation_model.history
    validation_results, validation_runtime = _evaluate_stage(
        "validation", validation_stage, validation_model, bands, config["top_k"], initial_train
    )
    del validation_model

    test_stage = BenchmarkStage.from_split(split, boundaries, "test")
    start = perf_counter()
    test_model = PopularModel.fit(test_stage.fit_events, test_stage.cutoff, fit_start=test_stage.fit_start, **metadata)
    test_build = perf_counter() - start
    test_results, test_runtime = _evaluate_stage("test", test_stage, test_model, bands, config["top_k"], initial_train)

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
        "provenance": {"code_sha256": code_sha256, "source_sha256": source_fingerprints(args.config), **_environment()},
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
