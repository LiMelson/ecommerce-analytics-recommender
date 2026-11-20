"""Validate ItemCF configurations, then score exactly one frozen test snapshot."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from data_algo.data_algo.path_utils import atomic_output
from data_algo.data_algo.schema import CORE_BEHAVIORS

from .benchmark import BenchmarkStage, History, HistoryBands, split_events, time_boundaries
from .itemcf import ItemCFConfig, ItemCFModel
from .popular import PopularModel, local_dataset_id
from .run import HERE, ROOT, _code_sha256, _environment, _evaluate_stage, _peak_rss_mib, source_fingerprints
from .sample import read_sample, sha256_file

BASE_CONFIG = HERE / "popular_v1.json"
FROZEN_POPULAR = HERE / "reports" / "p0-rec-01-benchmark.json"
SELECTION_ORDER = ["max_ndcg", "max_recall", "min_neighbors", "prefer_full_history", "config_order"]
RANKING_COUNTS = {
    "users_with_empty_recommendations",
    "users_with_less_than_k_recommendations",
    "effective_users_with_empty_recommendations",
}


def assert_shared_cohort(result, popular):
    keys = ("k", "users_known_in_initial_train", "future_items", "repeat_purchase_events")
    same = all(result[key] == popular[key] for key in keys)
    same &= result["coverage"]["candidate_catalog"] == popular["coverage"]["candidate_catalog"]
    for segment, group in result["groups"].items():
        reference = popular["groups"][segment]
        same &= all(
            value == reference["counts"][key] for key, value in group["counts"].items() if key not in RANKING_COUNTS
        )
        same &= all(group[scope]["users"] == reference[scope]["users"] for scope in ("primary", "all_purchase_targets"))
    if not same:
        raise ValueError("Evaluation cohort or candidates do not match frozen Popular v1")


def select_trial(trials):
    def preference(trial):
        primary = trial["evaluation"]["groups"]["overall"]["primary"]
        if primary["ndcg"] is None or primary["recall"] is None:
            raise ValueError("Cannot choose a configuration without effective validation users")
        parameters = trial["parameters"]
        return primary["ndcg"], primary["recall"], -parameters["neighbors"], parameters["max_history"] is None

    return max(trials, key=preference)


def load_study(path):
    study = json.loads(path.read_text(encoding="utf-8"))
    if study["selection_order"] != SELECTION_ORDER:
        raise ValueError("Unknown validation selection order")
    ids = [trial["id"] for trial in study["trials"]]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Study needs configurations with unique IDs")
    for trial in study["trials"]:
        ItemCFConfig(**{key: value for key, value in trial.items() if key != "id"})
    return study


def run_trial(phase, trial, stage, fallback, bands, initial_train, baseline, model_version, k):
    parameters = {key: value for key, value in trial.items() if key != "id"}
    config = ItemCFConfig(**parameters)
    start = perf_counter()
    model, diagnostics = ItemCFModel.fit(stage.fit_events, fallback, config, model_version=model_version)
    build_time = perf_counter() - start
    evaluation, timings = _evaluate_stage(phase, stage, model, bands, k, initial_train, track_item_sources=True)
    assert_shared_cohort(evaluation, baseline)
    print(f"{phase} {trial['id']}: {evaluation['groups']['overall']['primary']}", flush=True)
    return {
        "id": trial["id"],
        "parameters": parameters,
        "fit_statistics": diagnostics,
        "evaluation": evaluation,
        "runtime": {"fit_seconds": build_time, **timings, "process_peak_rss_mib_at_completion": _peak_rss_mib()},
    }


def verify_selection(report, study, protocol, source):
    if (
        report["study"] != study
        or report["protocol"] != protocol
        or any(report["provenance"][key] != value for key, value in source.items())
    ):
        raise ValueError("Validation report does not match current inputs and source; run validation again")
    expected = [(trial["id"], {key: value for key, value in trial.items() if key != "id"}) for trial in study["trials"]]
    actual = [(trial["id"], trial["parameters"]) for trial in report["trials"]]
    if expected != actual or select_trial(report["trials"])["id"] != report["selected_trial"]:
        raise ValueError("Validation report does not have the frozen configuration selection")
    return next(trial for trial in study["trials"] if trial["id"] == report["selected_trial"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "test"))
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=HERE / "itemcf_v1.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "p0-rec-02" / "itemcf")
    args = parser.parse_args()
    phase = "validation" if args.phase == "validate" else "test"
    output = args.out_dir / f"{phase}.json"
    if output.exists():
        parser.error(f"{output} already exists. Use a fresh output directory for a reproducibility run.")

    started = perf_counter()
    protocol = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    baseline = json.loads(FROZEN_POPULAR.read_text(encoding="utf-8"))["result"]
    if protocol != baseline["config"]:
        raise ValueError("Benchmark config differs from frozen Popular v1")
    study = load_study(args.config)
    source = {
        "code_sha256": _code_sha256(BASE_CONFIG, args.config),
        "source_sha256": source_fingerprints(BASE_CONFIG, args.config),
        "frozen_popular_report_sha256": sha256_file(FROZEN_POPULAR),
        "sample_sha256": protocol["sample_sha256"],
    }
    chosen = None
    if phase == "test":
        decision = args.out_dir / "validation.json"
        selection_fingerprint = sha256_file(decision)
        chosen = verify_selection(json.loads(decision.read_text(encoding="utf-8")), study, protocol, source)

    events, _, digest = read_sample(args.sample, protocol)
    boundaries = time_boundaries(protocol["observation_start"], protocol["observation_end_exclusive"])
    split = split_events(events, boundaries)
    stage = BenchmarkStage.from_split(split, boundaries, phase)
    stage.validate()
    bands = HistoryBands.fit(split[0])
    initial_train = History.fit(split[0], boundaries[1], fit_start=boundaries[0])
    k = protocol["top_k"]
    prepared = perf_counter()
    start = perf_counter()
    popular_version = protocol["validation_model_version"] if phase == "validation" else protocol["model_version"]
    fallback = PopularModel.fit(
        stage.fit_events,
        stage.cutoff,
        fit_start=stage.fit_start,
        dataset_id=local_dataset_id(digest),
        model_version=popular_version,
        sample_sha256=digest,
        protocol_version=protocol["protocol_version"],
        code_sha256=source["code_sha256"],
    )
    fallback_build_time = perf_counter() - start

    if phase == "validation":
        validation_prefix = study["model_version"] + "_validation_"
        trials = [
            run_trial(
                phase, trial, stage, fallback, bands, initial_train, baseline[phase], validation_prefix + trial["id"], k
            )
            for trial in study["trials"]
        ]
        result = {"trials": trials, "selected_trial": select_trial(trials)["id"]}
    else:
        trial = run_trial(
            phase, chosen, stage, fallback, bands, initial_train, baseline[phase], study["model_version"], k
        )
        result = {"frozen_validation_sha256": selection_fingerprint, "selected_trial": chosen["id"], "trial": trial}

    payload = {
        "phase": phase,
        "protocol": protocol,
        "study": study,
        "provenance": {**_environment(), **source},
        "windows": {
            "fit_start_inclusive": stage.fit_start.isoformat(),
            "fit_end_exclusive": stage.cutoff.isoformat(),
            "target_start_inclusive": stage.cutoff.isoformat(),
            "target_end_exclusive": stage.target_end.isoformat(),
            "fit_rows": stage.fit_rows,
            "target_rows": stage.target_rows,
        },
        "history_bands": vars(bands),
        "popular_fallback_model_metadata": vars(fallback.metadata),
        "frozen_popular_evaluation": baseline[phase],
        "rules": {
            "positive_behaviors": list(CORE_BEHAVIORS),
            "implicit_feedback": "binary_user_item",
            "similarity": "cosine",
            "aggregation": "sum_of_retained_neighbor_similarities",
            "history_limit_policy": "latest_distinct_items_then_item_id",
            "tie_break": "item_id_string_ascending",
            "test_trials": 1,
            "random_seed": None,
        },
        **result,
        "runtime": {
            "read_split_and_prepare_seconds": prepared - started,
            "popular_fallback_build_seconds": fallback_build_time,
            "total_seconds_before_output": perf_counter() - started,
            "process_peak_rss_mib": _peak_rss_mib(),
        },
    }
    with atomic_output(output) as temporary:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(f"{phase}: selected {result['selected_trial']} at {output.resolve()}")


if __name__ == "__main__":
    main()
