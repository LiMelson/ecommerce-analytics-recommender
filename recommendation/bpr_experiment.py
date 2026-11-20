"""Freeze a limited BPR validation study, then evaluate one final test model."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from data_algo.data_algo.path_utils import atomic_output

from .benchmark import BenchmarkStage, History, HistoryBands, split_events, time_boundaries
from .bpr import BPRConfig, BPRModel
from .bpr_io import load_model, save_model
from .itemcf_experiment import assert_shared_cohort
from .popular import PopularModel, local_dataset_id
from .run import HERE, ROOT, _code_sha256, _environment, _evaluate_stage, _peak_rss_mib, source_fingerprints
from .sample import read_sample, sha256_file

BASE = HERE / "popular_v1.json"
STUDY = HERE / "bpr_v1.json"
REFERENCES = {
    "popular": HERE / "reports/p0-rec-01-benchmark.json",
    "itemcf_validation": HERE / "reports/p0-rec-02-validation.json",
    "itemcf_test": HERE / "reports/p0-rec-02-test.json",
}
SELECTION = ["max_ndcg", "max_recall", "min_dimensions", "min_epochs", "config_order"]


def select_bpr_trial(trials):
    viable = [
        trial
        for trial in trials
        if trial["status"] == "succeeded"
        and trial["training"]["fit_status"] == "trained"
        and trial["evaluation"]["groups"]["overall"]["primary"]["ndcg"] is not None
    ]
    if not viable:
        raise ValueError("No usable validation model exists")

    def key(trial):
        scores = trial["evaluation"]["groups"]["overall"]["primary"]
        return scores["ndcg"], scores["recall"], -trial["parameters"]["dimensions"], -trial["parameters"]["epochs"]

    return max(viable, key=key)


def frozen_trial(report, study, protocol, provenance):
    if (
        report["study"] != study
        or report["protocol"] != protocol
        or any(report["provenance"].get(key) != value for key, value in provenance.items())
    ):
        raise ValueError("Validation report is incompatible with the current source, data or study")
    expected = [(t["id"], {k: v for k, v in t.items() if k != "id"}) for t in study["trials"]]
    if [(t["id"], t["parameters"]) for t in report["trials"]] != expected:
        raise ValueError("Validation trial list does not match the frozen study")
    chosen = select_bpr_trial(report["trials"])
    if chosen["id"] != report["selected_trial"]:
        raise ValueError("Validation winner does not match the frozen selection rule")
    return next(trial for trial in study["trials"] if trial["id"] == chosen["id"])


def loaded_rankings_match(model, loaded, users, k):
    for user in sorted(users):
        if model.recommend(user, k) != loaded.recommend(user, k):
            raise ValueError("Loaded BPR model changed a ranked recommendation")
    return len(users)


def run_trial(phase, trial, stage, fallback, bands, initial, baseline, model_version, provenance, out_dir, k):
    parameters = {key: value for key, value in trial.items() if key != "id"}
    start = perf_counter()
    model, training = BPRModel.fit(stage.fit_events, fallback, BPRConfig(**parameters), model_version=model_version)
    train_seconds = perf_counter() - start
    evaluation, timing = _evaluate_stage(phase, stage, model, bands, k, initial, track_item_sources=True)
    assert_shared_cohort(evaluation, baseline)
    artifact = out_dir / f"{phase}-{trial['id']}.bpr"
    save_model(model, artifact, provenance=provenance, training_summary=training, evaluation=evaluation)
    loaded = load_model(artifact, fallback, model_version=model_version, code_sha256=provenance["code_sha256"])
    compared = loaded_rankings_match(model, loaded, stage.target_events["user_id"].unique(), k)
    print(f"{phase} {trial['id']}: {evaluation['groups']['overall']['primary']}", flush=True)
    return {
        "id": trial["id"],
        "parameters": parameters,
        "status": "succeeded",
        "training": training,
        "evaluation": evaluation,
        "artifact": {"file": artifact.name, "sha256": sha256_file(artifact), "loaded_rankings_checked_users": compared},
        "runtime": {"training_seconds": train_seconds, **timing, "process_peak_rss_mib_at_completion": _peak_rss_mib()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "test"))
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=STUDY)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts/p0-rec-03")
    args = parser.parse_args()
    phase = "validation" if args.phase == "validate" else "test"
    output = args.out_dir / f"{phase}.json"
    if output.exists():
        parser.error(f"Result already exists at {output}. Preserve it and use a fresh directory for reproduction.")
    protocol = json.loads(BASE.read_text(encoding="utf-8"))
    study = json.loads(args.config.read_text(encoding="utf-8"))
    popular = json.loads(REFERENCES["popular"].read_text(encoding="utf-8"))["result"]
    if (
        protocol != popular["config"]
        or study["selection_order"] != SELECTION
        or study["positive_definition"] != "distinct_user_item_buy"
        or study["negative_definition"] != "uniform_known_catalog_minus_training_buy_positives"
    ):
        raise ValueError("Unsupported study or changed frozen benchmark")
    ids = [trial["id"] for trial in study["trials"]]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Study must have unique trial IDs")
    for trial in study["trials"]:
        BPRConfig(**{key: value for key, value in trial.items() if key != "id"})
    provenance = {
        **_environment(),
        "sample_sha256": protocol["sample_sha256"],
        "code_sha256": _code_sha256(BASE, args.config),
        "source_sha256": source_fingerprints(BASE, args.config),
        "reference_report_sha256": {key: sha256_file(path) for key, path in REFERENCES.items()},
    }
    chosen = None
    if phase == "test":
        validation_path = args.out_dir / "validation.json"
        selection_sha256 = sha256_file(validation_path)
        chosen = frozen_trial(json.loads(validation_path.read_text(encoding="utf-8")), study, protocol, provenance)
    started = perf_counter()
    events, _, digest = read_sample(args.sample, protocol)
    boundaries = time_boundaries(protocol["observation_start"], protocol["observation_end_exclusive"])
    split = split_events(events, boundaries)
    stage = BenchmarkStage.from_split(split, boundaries, phase)
    stage.validate()
    bands = HistoryBands.fit(split[0])
    initial = History.fit(split[0], boundaries[1], fit_start=boundaries[0])
    popular_version = protocol["validation_model_version"] if phase == "validation" else protocol["model_version"]
    fallback = PopularModel.fit(
        stage.fit_events,
        stage.cutoff,
        fit_start=stage.fit_start,
        dataset_id=local_dataset_id(digest),
        sample_sha256=digest,
        model_version=popular_version,
        protocol_version=protocol["protocol_version"],
        code_sha256=provenance["code_sha256"],
    )
    k = protocol["top_k"]
    if phase == "validation":
        trials = []
        for trial in study["trials"]:
            parameters = {key: value for key, value in trial.items() if key != "id"}
            try:
                result = run_trial(
                    phase,
                    trial,
                    stage,
                    fallback,
                    bands,
                    initial,
                    popular[phase],
                    study["model_version"] + "_validation_" + trial["id"],
                    provenance,
                    args.out_dir,
                    k,
                )
            except (ValueError, FloatingPointError) as error:
                result = {"id": trial["id"], "parameters": parameters, "status": "failed", "error": str(error)}
                print(f"Failed {trial['id']}: {error}", flush=True)
            trials.append(result)
        try:
            selected = select_bpr_trial(trials)["id"]
        except ValueError:
            selected = None
        result = {"trials": trials, "selected_trial": selected}
    else:
        trial = run_trial(
            phase,
            chosen,
            stage,
            fallback,
            bands,
            initial,
            popular[phase],
            study["model_version"],
            provenance,
            args.out_dir,
            k,
        )
        result = {"frozen_validation_sha256": selection_sha256, "selected_trial": chosen["id"], "trial": trial}
    itemcf = json.loads(REFERENCES["itemcf_" + phase].read_text(encoding="utf-8"))
    itemcf_eval = (
        next(t for t in itemcf["trials"] if t["id"] == itemcf["selected_trial"])
        if phase == "validation"
        else itemcf["trial"]
    )["evaluation"]
    payload = {
        "phase": phase,
        "protocol": protocol,
        "study": study,
        "provenance": provenance,
        "windows": {
            "fit_start": stage.fit_start.isoformat(),
            "cutoff": stage.cutoff.isoformat(),
            "target_end": stage.target_end.isoformat(),
            "fit_rows": stage.fit_rows,
            "target_rows": stage.target_rows,
        },
        "history_bands": vars(bands),
        "training_snapshot_sha256": fallback.training_snapshot_sha256,
        "references": {"popular": popular[phase], "itemcf": itemcf_eval},
        **result,
        "runtime": {"total_seconds_before_output": perf_counter() - started, "process_peak_rss_mib": _peak_rss_mib()},
    }
    with atomic_output(output) as temporary:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(f"{phase} selected={result['selected_trial']} report={output.resolve()}")
    if result["selected_trial"] is None:
        raise SystemExit("No usable validation model. Inspect the saved failed trials before testing.")


if __name__ == "__main__":
    main()
