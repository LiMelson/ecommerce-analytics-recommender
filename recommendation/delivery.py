"""Delivery commands keep offline construction separate from Python inference."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import pandas as pd

from data_algo.data_algo.path_utils import atomic_output

from .assets import AssetError, save_asset
from .benchmark import split_events, time_boundaries
from .bpr import BPRConfig, BPRModel
from .engine import RecommendationEngine
from .itemcf import ItemCFConfig, ItemCFModel
from .popular import PopularModel, local_dataset_id
from .run import HERE, _code_sha256, _environment, source_fingerprints
from .sample import read_sample, sha256_file

CONFIGS = tuple(HERE / name for name in ("popular_v1.json", "itemcf_v1.json", "bpr_v1.json"))


def build_assets(sample, out_dir):
    registry_path = out_dir / "registry.json"
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("Build requires a fresh output directory to preserve existing assets")
    protocol, itemcf_config, bpr_config = [json.loads(path.read_text(encoding="utf-8")) for path in CONFIGS]
    references = {}
    for name in ("p0-rec-01-benchmark", "p0-rec-02-test", "p0-rec-03-test"):
        path = HERE / "reports" / f"{name}.json"
        references[name] = (json.loads(path.read_text(encoding="utf-8")), sha256_file(path))
    if protocol != references["p0-rec-01-benchmark"][0]["result"]["config"]:
        raise ValueError("Protocol differs from frozen Popular baseline")
    selected = {}
    for kind, config, reference in (
        ("itemcf", itemcf_config, references["p0-rec-02-test"][0]),
        ("bpr", bpr_config, references["p0-rec-03-test"][0]),
    ):
        trial = next(trial for trial in config["trials"] if trial["id"] == reference["selected_trial"])
        parameters = {key: value for key, value in trial.items() if key != "id"}
        if parameters != reference["trial"]["parameters"] or reference["protocol"] != protocol:
            raise ValueError("Selected parameters or protocol do not match frozen reports")
        selected[kind] = parameters
    provenance = {
        **_environment(),
        "code_sha256": _code_sha256(*CONFIGS),
        "source_sha256": source_fingerprints(*CONFIGS),
        "reference_report_sha256": {name: value[1] for name, value in references.items()},
    }
    started = perf_counter()
    events, _, digest = read_sample(sample, protocol)
    boundaries = time_boundaries(protocol["observation_start"], protocol["observation_end_exclusive"])
    train, validation, _ = split_events(events, boundaries)
    fit = pd.concat((train, validation), ignore_index=True)
    popular = PopularModel.fit(
        fit,
        boundaries[2],
        fit_start=boundaries[0],
        dataset_id=local_dataset_id(digest),
        model_version=protocol["model_version"],
        sample_sha256=digest,
        protocol_version=protocol["protocol_version"],
        code_sha256=provenance["code_sha256"],
    )
    models = [("popular", popular, {}, {"purchase_count_rule": "buy_count_desc_item_id_asc"})]
    itemcf, item_stats = ItemCFModel.fit(
        fit, popular, ItemCFConfig(**selected["itemcf"]), model_version=itemcf_config["model_version"]
    )
    models.append(("itemcf", itemcf, selected["itemcf"], item_stats))
    bpr, bpr_stats = BPRModel.fit(fit, popular, BPRConfig(**selected["bpr"]), model_version=bpr_config["model_version"])
    models.append(("bpr", bpr, selected["bpr"], bpr_stats))
    entries = {}
    for kind, model, parameters, summary in models:
        filename = model.metadata.model_version + ".asset"
        save_asset(model, out_dir / filename, parameters=parameters, provenance=provenance, summary=summary)
        entries[model.metadata.model_version] = {
            "file": filename,
            "kind": kind,
            "sha256": sha256_file(out_dir / filename),
            "dataset_id": model.metadata.dataset_id,
            "code_sha256": model.metadata.code_sha256,
        }
    with atomic_output(registry_path) as temporary:
        temporary.write_text(
            json.dumps({"format": "recommendation-registry-v1", "models": entries}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return models, {
        "registry": str(registry_path.resolve()),
        "metadata": asdict(popular.metadata),
        "fit_start": boundaries[0].isoformat(),
        "fit_events": len(fit),
        "training_snapshot_sha256": popular.training_snapshot_sha256,
        "selected_parameters": selected,
        "build_and_validated_save_seconds": perf_counter() - started,
        "models": {
            kind: {"version": model.metadata.model_version, "parameters": params} for kind, model, params, _ in models
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--sample", type=Path, required=True)
    build.add_argument("--out-dir", type=Path, required=True)
    for name in ("inspect", "load", "predict", "batch"):
        command = sub.add_parser(name)
        command.add_argument("--registry", type=Path, required=True)
        command.add_argument("--model-version", required=True)
        if name in ("predict", "batch"):
            command.add_argument("--dataset-id", required=True)
            command.add_argument("--limit", type=int, default=10)
        if name == "predict":
            command.add_argument("--user-id", required=True)
        if name == "batch":
            command.add_argument("--users-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            _, result = build_assets(args.sample, args.out_dir)
        else:
            engine = RecommendationEngine.from_registry(args.registry, preload=False)
            engine.load(args.model_version)
            if args.command in ("inspect", "load"):
                result = engine.describe(args.model_version)
            elif args.command == "predict":
                result = engine.predict(
                    user_id=args.user_id, dataset_id=args.dataset_id, model_version=args.model_version, limit=args.limit
                )
            else:
                users = json.loads(args.users_json.read_text(encoding="utf-8"))
                if not isinstance(users, list) or len(users) > 10_000:
                    raise ValueError("users-json must contain an array of at most 10000 user IDs")
                started = perf_counter()
                responses = engine.predict_batch(
                    users, dataset_id=args.dataset_id, model_version=args.model_version, limit=args.limit
                )
                result = {
                    "requests": len(responses),
                    "items": sum(len(r["items"]) for r in responses),
                    "empty_results": sum(not r["items"] for r in responses),
                    "seconds": perf_counter() - started,
                }
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (AssetError, ValueError, OSError, KeyError) as error:
        parser.exit(
            2,
            json.dumps({"error": getattr(error, "code", "invalid_input"), "message": str(error)}, ensure_ascii=False)
            + "\n",
        )


if __name__ == "__main__":
    main()
