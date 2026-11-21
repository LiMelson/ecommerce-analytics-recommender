"""One preregistered selection, confirmation, descriptive pooling and historical backtest."""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..benchmark import METRICS, SEGMENTS, Labels, _purchase_sets, ranking_metrics, time_boundaries
from ..run import HERE, _environment, _peak_rss_mib
from ..sample import sha256_file
from .ranker import CONFIG, CONFIG_PATH, HybridStudy


def write_new(path, record):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def selection_user(user):
    return sha256(user.encode("utf-8")).digest()[0] < 128


@dataclass
class Prediction:
    ids: tuple
    sources: tuple
    metrics: dict | None
    elapsed_ms: float


class Window:
    def __init__(self, events, start, cutoff, end, digest):
        started = perf_counter()
        fit = events.loc[events.event_time.ge(start) & events.event_time.lt(cutoff)]
        targets = events.loc[events.event_time.ge(cutoff) & events.event_time.lt(end)]
        self.model = HybridStudy(fit, start, cutoff, digest)
        self.users = sorted(targets.user_id.unique())
        self.purchases = _purchase_sets(targets)
        self.cache = {}
        self.info = {
            "start": start.isoformat(),
            "cutoff": cutoff.isoformat(),
            "end": end.isoformat(),
            "fit_events": len(fit),
            "target_events": len(targets),
            "training_snapshot_sha256": self.model.popular.training_snapshot_sha256,
            "history_bands": vars(self.model.bands),
            "prepare_seconds": perf_counter() - started,
        }

    def labels(self, user):
        return Labels.for_user(user, self.purchases.get(user, frozenset()), self.model.popular.history).eligible

    def prediction(self, user, arm):
        key = user, arm
        if key not in self.cache:
            start = perf_counter()
            picks = self.model.rank(user, arm)
            ms = (perf_counter() - start) * 1000
            ids = tuple(item for item, _ in picks)
            history = self.model.popular.history
            if len(ids) != len(set(ids)) or len(ids) > CONFIG["k"] or any(item not in history.catalog for item in ids):
                raise ValueError("Invalid study candidates")
            if any(item in history.blocked(user) for item in ids):
                raise ValueError("Previously purchased item returned")
            self.cache[key] = Prediction(
                ids, tuple(src for _, src in picks), ranking_metrics(ids, self.labels(user), CONFIG["k"]), ms
            )
        return self.cache[key]


def paired_summary(rows):
    if not rows:
        return None
    values = np.array(rows)
    rng = np.random.default_rng(CONFIG["bootstrap"]["seed"])
    n = len(values)
    means = []
    for start in range(0, CONFIG["bootstrap"]["resamples"], 32):
        batch = min(32, CONFIG["bootstrap"]["resamples"] - start)
        means.append(values[rng.integers(n, size=(batch, n))].mean(axis=1))
    tail = (1 - CONFIG["bootstrap"]["confidence"]) / 2
    lo, hi = np.quantile(np.concatenate(means), [tail, 1 - tail], axis=0)
    return {
        metric: {"delta": delta, "lower": lower, "upper": upper}
        for metric, delta, lower, upper in zip(METRICS, values.mean(axis=0), lo, hi, strict=True)
    }


def analyze(window, users, arm):
    coverage, primary_coverage, sources = set(), set(), Counter()
    stats = Counter(
        dict.fromkeys(
            (
                "history_eligible",
                "changed_users",
                "effective_changed_users",
                "effective_entered",
                "effective_removed",
                "entered",
                "removed",
                "new_hits",
                "lost_hits",
                "improved_ndcg_users",
                "worse_ndcg_users",
            ),
            0,
        )
    )
    times, metrics, differences = [], [], []
    for user in users:
        base = window.prediction(user, "H0")
        trial = window.prediction(user, arm)
        targets = window.labels(user)
        added, removed = set(trial.ids) - set(base.ids), set(base.ids) - set(trial.ids)
        coverage.update(trial.ids)
        sources.update(trial.sources)
        times.append(trial.elapsed_ms)
        stats["entered"] += len(added)
        stats["removed"] += len(removed)
        stats["history_eligible"] += user in window.model.eligible
        stats["changed_users"] += trial.ids != base.ids
        if targets:
            primary_coverage.update(trial.ids)
            metrics.append([trial.metrics[m] for m in METRICS])
            differences.append([trial.metrics[m] - base.metrics[m] for m in METRICS])
            stats["new_hits"] += len(added & targets)
            stats["lost_hits"] += len(removed & targets)
            stats["effective_entered"] += len(added)
            stats["effective_removed"] += len(removed)
            stats["effective_changed_users"] += trial.ids != base.ids
            stats["improved_ndcg_users"] += trial.metrics["ndcg"] > base.metrics["ndcg"]
            stats["worse_ndcg_users"] += trial.metrics["ndcg"] < base.metrics["ndcg"]
    catalog = len(window.model.popular.history.catalog)
    return {
        "window_users": len(users),
        "effective": len(metrics),
        "counts": dict(stats),
        "metrics": dict(zip(METRICS, np.mean(metrics, axis=0), strict=True)) if metrics else None,
        "paired_vs_H0": paired_summary(differences),
        "position_sources": dict(sources),
        "catalog": catalog,
        "coverage_items": len(primary_coverage),
        "coverage": len(primary_coverage) / catalog,
        "all_coverage_items": len(coverage),
        "all_coverage": len(coverage) / catalog,
        "rank_p50_ms": float(np.median(times)) if times else None,
        "rank_p95_ms": float(np.percentile(times, 95)) if times else None,
    }


def evaluate_arms(window, users, arms):
    model = window.model
    groups = {band: [] for band in SEGMENTS}
    for user in users:
        groups[model.bands.segment(model.popular.history.event_counts.get(user, 0))].append(user)
    return {
        arm: {group: analyze(window, subset, arm) for group, subset in {"overall": users, **groups}.items()}
        for arm in arms
    }


def run(sample, out):
    out.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    protocol = json.loads((HERE / "popular_v1.json").read_text(encoding="utf-8"))
    digest = sha256_file(sample)
    if digest != protocol["sample_sha256"]:
        raise ValueError("Unexpected sample SHA-256")
    start, cutoff, end, _ = time_boundaries(protocol["observation_start"], protocol["observation_end_exclusive"])
    table = pq.read_table(sample, filters=[("event_time", "<", end.tz_localize(None).to_pydatetime())])
    events = table.to_pandas()
    del table
    events["event_time"] = pd.to_datetime(events.event_time, utc=True)
    if not (events.event_time.ge(start) & events.event_time.lt(end)).all():
        raise ValueError("Invalid research time window")
    val = Window(events, start, cutoff, end, digest)
    choose = [u for u in val.users if selection_user(u)]
    confirm = [u for u in val.users if not selection_user(u)]
    selection = evaluate_arms(val, choose, CONFIG["arms"])
    winner = max(CONFIG["arms"][1:], key=lambda arm: selection[arm]["overall"]["metrics"]["ndcg"])
    lock = {
        "selected": winner,
        "rule": CONFIG["selection"],
        "selection": selection,
        "protocol_sha256": sha256_file(CONFIG_PATH),
    }
    write_new(out / "selection-lock.json", lock)
    confirmation = evaluate_arms(val, confirm, ["H0", winner])
    full = evaluate_arms(val, val.users, CONFIG["arms"])
    reference = json.loads((HERE / "reports/p0-rec-01-benchmark.json").read_text(encoding="utf-8"))
    original = reference["result"]["validation"]["groups"]["overall"]["primary"]
    baseline = full["H0"]["overall"]
    if baseline["effective"] != original["users"] or any(
        abs(baseline["metrics"][m] - original[m]) > 1e-12 for m in METRICS
    ):
        raise ValueError("Pooled H0 failed frozen validation check")
    validation_info = val.info
    del val
    roll_cutoff = start + (cutoff - start) * CONFIG["rolling_fraction"]
    rolling = Window(events, start, roll_cutoff, cutoff, digest)
    backtest = evaluate_arms(rolling, rolling.users, ["H0", winner])
    decision = confirmation[winner]["overall"]
    paired = decision["paired_vs_H0"]
    gates = CONFIG["success"]
    checks = {
        "confirmation_ndcg": paired["ndcg"]["delta"] >= gates["min_confirmation_ndcg_delta"],
        "paired_lower": paired["ndcg"]["lower"] > gates["min_ndcg_lower_exclusive"],
        "recall": paired["recall"]["delta"] >= gates["min_recall_delta"],
        "common_users": all(confirmation[a]["overall"]["effective"] == decision["effective"] for a in confirmation),
        "rolling": backtest[winner]["overall"]["paired_vs_H0"]["ndcg"]["delta"] >= gates["min_rolling_ndcg_delta"],
        "latency": max(block[winner]["overall"]["rank_p95_ms"] for block in (confirmation, full, backtest))
        <= gates["max_p95_ms"],
    }
    checks = {name: bool(value) for name, value in checks.items()}
    result = {
        "selection": selection,
        "confirmation": confirmation,
        "pooled": full,
        "backtest": backtest,
        "selected": winner,
        "checks": checks,
        "passes": all(checks.values()),
        "environment": _environment(),
        "validation_window": validation_info,
        "rolling_window": rolling.info,
        "sample_sha256": digest,
        "selection_lock_sha256": sha256_file(out / "selection-lock.json"),
        "protocol_sha256": sha256_file(CONFIG_PATH),
        "elapsed_seconds": perf_counter() - started,
        "peak_process_rss_mib": _peak_rss_mib(),
    }
    write_new(out / "results.json", result)
    print(
        json.dumps({k: result[k] for k in ("selected", "checks", "passes", "elapsed_seconds", "peak_process_rss_mib")})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    run(args.sample, args.out_dir)
