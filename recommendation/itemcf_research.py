"""Diagnose and run the fixed ItemCF ablation on the validation window only."""

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from math import ceil
from pathlib import Path
from time import perf_counter

import numpy as np

from data_algo.data_algo.path_utils import atomic_output

from .benchmark import (
    BenchmarkStage,
    HistoryBands,
    Labels,
    _macro,
    _purchase_sets,
    nearest_rank,
    ranking_metrics,
    time_boundaries,
)
from .itemcf import ItemCFConfig, ItemCFModel
from .itemcf_experiment import assert_shared_cohort
from .popular import PopularModel, local_dataset_id
from .run import HERE, ROOT, _code_sha256, _environment, _evaluate_stage, _peak_rss_mib, source_fingerprints
from .sample import read_sample, sha256_file
from .weighted_itemcf import BEHAVIORS, InteractionFactors, fit_experimental_itemcf

PROTOCOL_PATH = HERE / "popular_v1.json"
STUDY_PATH = HERE / "itemcf_research_v1.json"
REPORTS = HERE / "reports"
REFERENCES = {
    "popular_v1": REPORTS / "p0-rec-01-benchmark.json",
    "itemcf_v1": REPORTS / "p0-rec-02-validation.json",
    "bpr_v1": REPORTS / "p0-rec-03-validation.json",
}


def validation_stage(events, protocol):
    start, cutoff, end, _ = time_boundaries(protocol["observation_start"], protocol["observation_end_exclusive"])
    times = events["event_time"]
    fit = events.loc[times.ge(start) & times.lt(cutoff)]
    targets = events.loc[times.ge(cutoff) & times.lt(end)]
    stage = BenchmarkStage(fit, targets, start, cutoff, end, len(fit), len(targets))
    stage.validate()
    return stage


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _chosen_trial(report, protocol):
    if report["phase"] != "validation" or report["protocol"] != protocol:
        raise ValueError("Baseline report must be from the frozen validation protocol")
    return next(trial for trial in report["trials"] if trial["id"] == report["selected_trial"])


@dataclass(frozen=True)
class StudyContext:
    stage: BenchmarkStage
    protocol: dict
    study: dict
    popular: PopularModel
    bands: HistoryBands
    config: ItemCFConfig
    references: dict
    bpr_training: dict
    provenance: dict

    @property
    def k(self):
        return self.protocol["top_k"]


def prepare(sample):
    protocol = _json(PROTOCOL_PATH)
    study = _json(STUDY_PATH)
    popular_report = _json(REFERENCES["popular_v1"])["result"]
    itemcf = _chosen_trial(_json(REFERENCES["itemcf_v1"]), protocol)
    bpr = _chosen_trial(_json(REFERENCES["bpr_v1"]), protocol)
    if (
        protocol != popular_report["config"]
        or study["phase"] != "validation"
        or itemcf["id"] != study["baseline_trial"]
    ):
        raise ValueError("Study does not match the frozen protocol and ItemCF control")
    references = {
        "popular_v1": popular_report["validation"],
        "itemcf_v1": itemcf["evaluation"],
        "bpr_v1": bpr["evaluation"],
    }
    for evaluation in references.values():
        assert_shared_cohort(evaluation, references["popular_v1"])
    events, _, digest = read_sample(sample, protocol)
    stage = validation_stage(events, protocol)
    code_sha = _code_sha256(PROTOCOL_PATH, STUDY_PATH)
    popular = PopularModel.fit(
        stage.fit_events,
        stage.cutoff,
        fit_start=stage.fit_start,
        dataset_id=local_dataset_id(digest),
        model_version=protocol["validation_model_version"],
        sample_sha256=digest,
        protocol_version=protocol["protocol_version"],
        code_sha256=code_sha,
    )
    provenance = {
        **_environment(),
        "sample_sha256": digest,
        "training_snapshot_sha256": popular.training_snapshot_sha256,
        "study_sha256": sha256_file(STUDY_PATH),
        "code_sha256": code_sha,
        "source_sha256": source_fingerprints(PROTOCOL_PATH, STUDY_PATH),
        "validation_reference_report_sha256": {name: sha256_file(path) for name, path in REFERENCES.items()},
    }
    return StudyContext(
        stage,
        protocol,
        study,
        popular,
        HistoryBands.fit(stage.fit_events),
        ItemCFConfig(**itemcf["parameters"]),
        references,
        bpr["training"],
        provenance,
    )


def _quantiles(values):
    sorted_values = sorted(values)
    return {
        f"p{p}": int(nearest_rank(sorted_values, p / 100)) if sorted_values else None
        for p in (25, 50, 75, 90, 95, 99, 100)
    }


def _behavior_summary(events):
    return {
        str(name): {"events": int(count), "share": count / len(events)}
        for name, count in events["behavior_type"].value_counts().items()
    }


def _baseline_summary(result):
    users = result["groups"]["overall"]["counts"]["users"]
    source = result["recommendation_source_users"]
    summary = {
        "groups": result["groups"],
        "coverage": result["coverage"],
        "recommendation_source_users": source,
        "any_popular_user_rate": sum(count for key, count in source.items() if "popular" in key) / users,
        "rankings_sha256": result["rankings_sha256"],
    }
    if "recommendation_item_sources" in result:
        items = result["recommendation_item_sources"]
        summary["recommendation_item_sources"] = items
        summary["popular_item_rate"] = sum(count for key, count in items.items() if "popular" in key) / sum(
            items.values()
        )
    return summary


def diagnose(ctx):
    stage = ctx.stage
    start = perf_counter()
    binary, diagnostics = ItemCFModel.fit(stage.fit_events, ctx.popular, ctx.config, model_version="itemcf_v1")
    binary_fit_seconds = perf_counter() - start
    degrees = np.diff(binary._neighbors.indptr)
    history_degrees = stage.fit_events.groupby("user_id")["item_id"].nunique()
    purchases = stage.fit_events.loc[stage.fit_events["behavior_type"].eq("buy")]
    positive_degrees = purchases.groupby("user_id")["item_id"].nunique()
    positive_counts = positive_degrees.reindex(history_degrees.index, fill_value=0)
    target_purchases = _purchase_sets(stage.target_events)
    all_popular_items = {candidate.item_id for candidate in ctx.popular.ordered[: ctx.k]}
    eligible = [
        Labels.for_user(user, observed, ctx.popular.history).eligible for user, observed in target_purchases.items()
    ]
    return {
        "behaviors": {"fit": _behavior_summary(stage.fit_events), "validation": _behavior_summary(stage.target_events)},
        "distinct_history_items_per_training_user": _quantiles(history_degrees),
        "purchase_positives": {
            "events": len(purchases),
            "training_users": len(history_degrees),
            "nonbuy_users": int(positive_counts.eq(0).sum()),
            "purchase_users": len(positive_degrees),
            "unique_positive_pairs": int(positive_degrees.sum()),
            "positive_pairs_per_user_including_zeros": _quantiles(positive_counts),
            "positive_pairs_per_buyer": _quantiles(positive_degrees),
            "distinct_buy_items": purchases["item_id"].nunique(),
            "trainable_pairs_density": positive_degrees.sum() / (len(history_degrees) * len(binary.items)),
        },
        "itemcf_neighbor_degrees": {
            "p_nearest_rank": _quantiles(degrees),
            "counts_by_retained_neighbor_count": dict(enumerate(np.bincount(degrees).tolist())),
            "fit_statistics": diagnostics,
            "fit_seconds": binary_fit_seconds,
        },
        "bpr": {
            **{
                key: ctx.bpr_training[key]
                for key in (
                    "trainable_users",
                    "trainable_positive_pairs",
                    "catalog_items",
                    "recommendable_signal_items",
                    "candidate_items_without_positive_signal",
                )
            },
            "fit_trainable_user_rate": ctx.bpr_training["trainable_users"] / len(history_degrees),
        },
        "popularity_concentration": {
            "training_buy_events_for_first_10_items": sum(
                candidate.purchase_count for candidate in ctx.popular.ordered[: ctx.k]
            ),
            "validation_eligible_pairs_in_first_10_items": sum(len(items & all_popular_items) for items in eligible),
            "validation_all_eligible_pairs": sum(map(len, eligible)),
            "validation_eligible_users_with_a_target_in_first_10_items": sum(
                bool(items & all_popular_items) for items in eligible
            ),
        },
    }


class _RankCapture:
    """Keep ordered IDs in memory for paired evaluation, without ever writing them."""

    def __init__(self, model):
        self.model = model
        self.metadata = model.metadata
        self.history = model.history
        self.rankings = {}

    def recommend(self, user_id, k):
        ranking = self.model.recommend(user_id, k)
        self.rankings[user_id] = tuple(item.item_id for item in ranking.items)
        return ranking


def _research_cohorts(ctx):
    history = ctx.popular.history
    fit = ctx.stage.fit_events
    browsers_or_carters = set(fit.loc[fit["behavior_type"].isin(("pv", "cart")), "user_id"])
    engaged_no_buy = browsers_or_carters - history.purchased.keys()
    observed = _purchase_sets(ctx.stage.target_events)
    labels = {user: Labels.for_user(user, items, history) for user, items in observed.items()}
    bought = [item.item_id for item in ctx.popular.ordered if item.purchase_count]
    head_size = ceil(len(bought) * 0.01)
    buckets = {
        "training_purchase_head": frozenset(bought[:head_size]),
        "training_purchase_tail": frozenset(bought[head_size:]),
        "zero_training_purchases": history.catalog - set(bought),
    }
    return engaged_no_buy, labels, buckets


def _cohort_summary(rankings, ctx, engaged_no_buy, labels, buckets, control_ranks):
    primary_scores, all_scores = [], []
    exposed, exposed_warm, exposed_no_buy = set(), set(), set()
    eligible_pairs, hit_pairs = Counter(), Counter()
    global_change = Counter()
    in_no_buy_cohort = 0
    for user, ranking in rankings.items():
        items = set(ranking)
        exposed.update(items)
        cold = user not in ctx.popular.history.event_counts
        no_buy = user in engaged_no_buy
        if not cold:
            exposed_warm.update(items)
        if control_ranks is not None and ranking != control_ranks[user]:
            global_change.update(["all", ctx.bands.segment(ctx.popular.history.event_counts.get(user, 0))])
            if no_buy:
                global_change["engaged_no_buy"] += 1
        if user in labels:
            label = labels[user]
            hits = items & label.eligible
            for bucket, catalog in buckets.items():
                eligible_pairs[bucket] += len(label.eligible & catalog)
                hit_pairs[bucket] += len(hits & catalog)
            if no_buy:
                all_scores.append(ranking_metrics(ranking, label.observed, ctx.k))
                if label.eligible:
                    primary_scores.append(ranking_metrics(ranking, label.eligible, ctx.k))
        if no_buy:
            in_no_buy_cohort += 1
            exposed_no_buy.update(items)
    return {
        "engaged_no_buy": {
            "window_users": in_no_buy_cohort,
            "primary": _macro(primary_scores),
            "all_purchase_targets": _macro(all_scores),
            "distinct_recommended_items": len(exposed_no_buy),
        },
        "users_with_different_ordered_top_k_vs_A": (
            {name: global_change[name] for name in ("all", "cold", "low", "medium", "high", "engaged_no_buy")}
            if control_ranks is not None
            else None
        ),
        "candidate_buckets": {
            key: {
                "candidate_items": len(catalog),
                "distinct_recommended_items": len(exposed & catalog),
                "warm_users_distinct_recommended_items": len(exposed_warm & catalog),
                "eligible_purchase_pairs": eligible_pairs[key],
                "hit_purchase_pairs": hit_pairs[key],
                "micro_recall": hit_pairs[key] / eligible_pairs[key] if eligible_pairs[key] else None,
            }
            for key, catalog in buckets.items()
        },
    }


def paired_intervals(labels, control_ranks, trial_ranks, k, bootstrap):
    """Percentile bootstrap of per-user differences over the primary denominator."""
    differences = []
    for user in sorted(labels):
        eligible = labels[user].eligible
        if eligible:
            before = ranking_metrics(control_ranks[user], eligible, k)
            after = ranking_metrics(trial_ranks[user], eligible, k)
            differences.append([after[name] - before[name] for name in ("ndcg", "recall")])
    if not differences:
        raise ValueError("Paired bootstrap needs eligible validation users")
    diffs = np.asarray(differences)
    rng = np.random.default_rng(bootstrap["seed"])
    n = len(diffs)
    resamples = bootstrap["resamples"]
    distribution = np.empty((resamples, 2))
    for start in range(0, resamples, 64):
        end = min(start + 64, resamples)
        draw = rng.integers(n, size=(end - start, n))
        distribution[start:end] = diffs[draw].mean(axis=1)
    tail = (1 - bootstrap["confidence"]) / 2
    lower, upper = np.quantile(distribution, [tail, 1 - tail], axis=0)
    return {
        "paired_users": n,
        **{
            name: {"mean_delta": float(diffs[:, i].mean()), "ci_lower": float(lower[i]), "ci_upper": float(upper[i])}
            for i, name in enumerate(("ndcg", "recall"))
        },
    }


def run_ablation(ctx):
    engaged_no_buy, labels, buckets = _research_cohorts(ctx)
    popular_ranks = {
        user: tuple(ctx.popular.rank_ids(user, ctx.k)) for user in ctx.stage.target_events["user_id"].unique()
    }
    cohorts = _cohort_summary(popular_ranks, ctx, engaged_no_buy, labels, buckets, None)
    trials = []
    control = None
    for trial in ctx.study["trials"]:
        start = perf_counter()
        row = {**trial, "status": "failed"}
        trials.append(row)
        try:
            if trial["id"] == "A":
                model, stats = ItemCFModel.fit(ctx.stage.fit_events, ctx.popular, ctx.config, model_version="itemcf_v1")
            else:
                weights = (
                    tuple(ctx.study["behavior_weights"][name] for name in BEHAVIORS) if trial["weighted"] else None
                )
                half_life = ctx.study["half_life_days"] if trial["decayed"] else None
                factors = InteractionFactors(weights, half_life)
                model, stats = fit_experimental_itemcf(ctx.stage.fit_events, ctx.popular, ctx.config, factors)
            if model.metadata.model_version != trial["model_version"]:
                raise ValueError("Unexpected ablation model version")
            row["fit_statistics"] = stats
            row["runtime"] = {"fit_seconds": perf_counter() - start, "process_peak_rss_mib_after_fit": _peak_rss_mib()}
            capture = _RankCapture(model)
            summary, runtime = _evaluate_stage(
                "validation",
                ctx.stage,
                capture,
                ctx.bands,
                ctx.k,
                ctx.popular.history,
                track_item_sources=True,
            )
            row["runtime"].update(runtime)
            assert_shared_cohort(summary, ctx.references["popular_v1"])
            if trial["id"] == "A":
                if summary["rankings_sha256"] != ctx.references["itemcf_v1"]["rankings_sha256"]:
                    raise ValueError("Control rankings differ from the frozen ItemCF validation report")
            row["additional_groups"] = _cohort_summary(capture.rankings, ctx, engaged_no_buy, labels, buckets, control)
            row["paired_vs_A"] = (
                paired_intervals(labels, control, capture.rankings, ctx.k, ctx.study["bootstrap"]) if control else None
            )
            if trial["id"] == "A":
                control = capture.rankings
            row["evaluation"] = summary
            row["sources"] = {key: value for key, value in _baseline_summary(summary).items() if key.endswith("_rate")}
            row["status"] = "ok"
            print(f"{trial['id']}: {summary['groups']['overall']['primary']}", flush=True)
        except (ValueError, RuntimeError, MemoryError) as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{trial['id']}: {row['error']}", flush=True)
        row["elapsed_seconds"] = perf_counter() - start
    selected = None
    if all(row["status"] == "ok" for row in trials):
        ndcg, recall = ctx.study["selection_order"][:2]
        scores = [trial["evaluation"]["groups"]["overall"]["primary"] for trial in trials]
        best_index = max(range(len(scores)), key=lambda i: (scores[i][ndcg], scores[i][recall]))
        selected = trials[best_index]["id"]
    return {
        "trials": trials,
        "validation_preferred_trial": selected,
        "popular_additional_groups": cohorts,
        "bpr_engaged_no_buy_equivalence": "The frozen BPR model calls Popular for users without a training purchase.",
        "long_tail_definition": "Top 1% of items purchased in fit, ordered by purchase event count then item ID; purchased remainder; zero fit purchases.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("diagnose", "validate"))
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "p0-rec-05")
    args = parser.parse_args()
    output = args.out_dir / ("diagnosis.json" if args.phase == "diagnose" else "validation.json")
    if output.exists():
        parser.error(f"{output} already exists; choose a fresh output directory")
    started = perf_counter()
    ctx = prepare(args.sample)
    result = {"diagnosis": diagnose(ctx)} if args.phase == "diagnose" else run_ablation(ctx)
    payload = {
        "phase": "validation",
        "protocol": ctx.protocol,
        "study": ctx.study,
        "provenance": ctx.provenance,
        "fit_config": asdict(ctx.config),
        "windows": {
            "fit_start_inclusive": ctx.stage.fit_start.isoformat(),
            "fit_end_exclusive": ctx.stage.cutoff.isoformat(),
            "validation_end_exclusive": ctx.stage.target_end.isoformat(),
            "fit_rows": ctx.stage.fit_rows,
            "validation_rows": ctx.stage.target_rows,
        },
        "history_bands": asdict(ctx.bands),
        "historical_validation_baselines": {name: _baseline_summary(result) for name, result in ctx.references.items()},
        "future_items": ctx.references["popular_v1"]["future_items"],
        **result,
        "runtime": {"seconds": perf_counter() - started, "process_peak_rss_mib": _peak_rss_mib()},
    }
    with atomic_output(output) as temp:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(output.resolve())
    if args.phase == "validate" and result["validation_preferred_trial"] is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
