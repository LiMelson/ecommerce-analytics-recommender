import json
import subprocess
import sys
from dataclasses import asdict
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from test_bpr import CUTOFF, META, START, frame

from recommendation import assets
from recommendation.assets import AssetError, load_asset, save_asset
from recommendation.bpr import BPRConfig, BPRModel
from recommendation.engine import RecommendationEngine
from recommendation.itemcf import ItemCFConfig, ItemCFModel
from recommendation.popular import PopularModel
from recommendation.sample import sha256_file


@pytest.fixture
def bundle():
    data = frame(
        ("buy", "A", "buy"),
        ("buy", "A", "buy"),
        ("buy", "B", "pv"),
        ("other", "B", "buy"),
        ("other", "C", "pv"),
        ("cart", "C", "cart"),
        ("view", "D", "pv"),
        ("solo", "E", "pv"),
    )
    popular = PopularModel.fit(data, CUTOFF, fit_start=START, **META)
    item_config = ItemCFConfig(3, 2)
    itemcf, item_summary = ItemCFModel.fit(data, popular, item_config, model_version="itemcf_v1")
    bpr_config = BPRConfig(dimensions=4, epochs=3, negatives_per_positive=2)
    bpr, bpr_summary = BPRModel.fit(data, popular, bpr_config, model_version="bpr_mf_buy_v1")
    return [
        (popular, {}, {}),
        (itemcf, asdict(item_config), item_summary),
        (bpr, asdict(bpr_config), bpr_summary),
    ]


def save_bundle(tmp_path, bundle):
    entries = {}
    for model, params, summary in bundle:
        path = tmp_path / f"{model.metadata.model_version}.asset"
        save_asset(
            model, path, parameters=params, provenance={"code_sha256": model.metadata.code_sha256}, summary=summary
        )
        entries[model.metadata.model_version] = {
            "file": path.name,
            "dataset_id": model.metadata.dataset_id,
            "sha256": sha256_file(path),
            "code_sha256": model.metadata.code_sha256,
            "kind": {PopularModel: "popular", ItemCFModel: "itemcf", BPRModel: "bpr"}[type(model)],
        }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"format": "recommendation-registry-v1", "models": entries}), encoding="utf-8")
    return path


def test_three_models_roundtrip_continuous_batch_and_exact_contract(bundle, tmp_path, monkeypatch):
    path = save_bundle(tmp_path, bundle)
    original = RecommendationEngine.from_models([model for model, _, _ in bundle])
    for cls in (PopularModel, ItemCFModel, BPRModel):
        monkeypatch.setattr(cls, "fit", lambda *_args, **_kwargs: pytest.fail("Inference called fit"))
    restored = RecommendationEngine.from_registry(path)
    for model, _, _ in bundle:
        request = {"dataset_id": model.metadata.dataset_id, "model_version": model.metadata.model_version, "limit": 10}
        users = ["new", "view", "cart", "buy", "other", "solo", "buy"]
        baseline = [original.predict(user_id=user, **request) for user in users]
        assert restored.predict_batch(users, **request) == baseline
        for user, expected in zip(users, baseline, strict=True):
            assert restored.predict(user_id=user, **request) == expected
            assert set(expected) == {"user_id", "dataset_id", "model_version", "items"}
            ids = [item["item_id"] for item in expected["items"]]
            assert len(ids) == len(set(ids))
            assert set(ids).isdisjoint(model.history.blocked(user))
            assert [item["rank"] for item in expected["items"]] == list(range(1, len(ids) + 1))
            assert all(set(item) == {"item_id", "category_id", "rank", "score", "reason"} for item in expected["items"])


def test_models_for_one_dataset_must_share_the_training_snapshot(bundle, tmp_path):
    _, itemcf_config, _ = bundle[1]
    data = frame(("other", "B", "buy"), ("new-train-user", "A", "pv"))
    other_fallback = PopularModel.fit(data, CUTOFF, fit_start=START, **META)
    itemcf, summary = ItemCFModel.fit(data, other_fallback, ItemCFConfig(**itemcf_config), model_version="itemcf_v1")
    popular = bundle[0][0]
    assert itemcf.metadata.train_cutoff_exclusive == popular.metadata.train_cutoff_exclusive
    assert other_fallback.training_snapshot_sha256 != popular.training_snapshot_sha256

    with pytest.raises(AssetError, match="training snapshot") as failure:
        RecommendationEngine.from_models([popular, itemcf])
    assert failure.value.code == "model_identity_mismatch"

    path = save_bundle(tmp_path, [bundle[0], (itemcf, itemcf_config, summary)])
    engine = RecommendationEngine.from_registry(path, preload=False)
    engine.load("popular_v1")
    with pytest.raises(AssetError, match="training snapshot") as failure:
        engine.load("itemcf_v1")
    assert failure.value.code == "model_identity_mismatch"
    assert engine.describe("popular_v1")["loaded"]
    with pytest.raises(AssetError) as failure:
        engine.describe("itemcf_v1")
    assert failure.value.code == "model_not_loaded"
    with pytest.raises(AssetError, match="training snapshot"):
        RecommendationEngine.from_registry(path)


@pytest.mark.parametrize(
    ("module", "function", "arguments"),
    [("delivery_check", "verify_real", "None, None"), ("http_check", "verify_http", "None, None, None")],
)
def test_acceptance_checks_refuse_optimized_python(module, function, arguments):
    command = f"from recommendation.{module} import {function}; {function}({arguments})"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-O", "-c", command], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "without Python optimization so all checks execute" in result.stderr


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("user_id", "", "invalid_user_id"),
        ("user_id", " u", "invalid_user_id"),
        ("user_id", 12, "invalid_user_id"),
        ("user_id", "x\n", "invalid_user_id"),
        ("model_version", "fake", "unknown_model"),
        ("dataset_id", "website-not-bound", "dataset_mismatch"),
        ("limit", 0, "invalid_limit"),
        ("limit", 11, "invalid_limit"),
        ("limit", True, "invalid_limit"),
    ],
)
def test_request_errors(bundle, field, value, code):
    engine = RecommendationEngine.from_models([model for model, _, _ in bundle])
    request = {"user_id": "buy", "dataset_id": META["dataset_id"], "model_version": "popular_v1", "limit": 10}
    with pytest.raises(AssetError) as failure:
        engine.predict(**{**request, field: value})
    assert failure.value.code == code


def test_not_loaded_missing_and_corrupt_assets_fail_explicitly(bundle, tmp_path):
    path = save_bundle(tmp_path, bundle)
    engine = RecommendationEngine.from_registry(path, preload=False)
    with pytest.raises(AssetError, match="not been loaded") as failure:
        engine.predict(user_id="buy", dataset_id=META["dataset_id"], model_version="popular_v1")
    assert failure.value.code == "model_not_loaded"
    engine.load("popular_v1")
    expected = engine.predict(user_id="buy", dataset_id=META["dataset_id"], model_version="popular_v1")
    asset = tmp_path / "popular_v1.asset"
    asset.write_bytes(b"broken")
    with pytest.raises(AssetError) as failure:
        engine.load("popular_v1")
    assert failure.value.code == "model_corrupt"
    assert engine.predict(user_id="buy", dataset_id=META["dataset_id"], model_version="popular_v1") == expected
    asset.unlink()
    with pytest.raises(AssetError) as failure:
        engine.load("popular_v1")
    assert failure.value.code == "model_data_missing"


@pytest.mark.parametrize("index", [0, 1, 2])
def test_identity_and_file_corruption_detection(bundle, tmp_path, index):
    path = save_bundle(tmp_path, bundle)
    model = bundle[index][0]
    asset = tmp_path / f"{model.metadata.model_version}.asset"
    expected = {
        "expected_dataset_id": META["dataset_id"],
        "expected_model_version": model.metadata.model_version,
        "expected_code_sha256": META["code_sha256"],
    }
    for field, wrong in (
        ("expected_dataset_id", "a" * 32),
        ("expected_model_version", "wrong"),
        ("expected_code_sha256", "wrong"),
    ):
        with pytest.raises(AssetError):
            load_asset(asset, **{**expected, field: wrong})
    with ZipFile(asset) as z:
        members = {name: z.read(name) for name in z.namelist()}
    members["popular.json"] += b" "
    with ZipFile(asset, "w", ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    with pytest.raises(AssetError, match="checksum"):
        load_asset(asset, **expected)
    assert path.is_file()


def test_atomic_failed_save_preserves_previous_asset(bundle, tmp_path, monkeypatch):
    save_bundle(tmp_path, bundle)
    model, params, summary = bundle[0]
    path = tmp_path / "popular_v1.asset"
    previous = path.read_bytes()
    original = assets.ZipFile.writestr

    def broken_write(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated interrupted save")

    monkeypatch.setattr(assets.ZipFile, "writestr", broken_write)
    with pytest.raises(OSError, match="interrupted"):
        save_asset(model, path, parameters=params, provenance={"code_sha256": META["code_sha256"]}, summary=summary)
    assert path.read_bytes() == previous


def test_loaded_state_immutable_and_inspection_cannot_mutate_metadata(bundle, tmp_path):
    registry = save_bundle(tmp_path, bundle)
    engine = RecommendationEngine.from_registry(registry)
    description = engine.describe("itemcf_v1")
    description["model"]["dataset_id"] = "wrong"
    description["parameters"]["neighbors"] = 999
    assert engine.describe("itemcf_v1")["parameters"]["neighbors"] == 3
    for model, _, _ in bundle:
        restored, _ = load_asset(
            tmp_path / f"{model.metadata.model_version}.asset",
            expected_dataset_id=META["dataset_id"],
            expected_model_version=model.metadata.model_version,
            expected_code_sha256=META["code_sha256"],
        )
        with pytest.raises(TypeError):
            restored.history.event_counts["buy"] = 0
        if isinstance(restored, ItemCFModel):
            with pytest.raises(ValueError):
                restored._neighbors.data[0] = 0
            with pytest.raises(TypeError):
                restored._user_rows["buy"] = 999
        if isinstance(restored, BPRModel):
            with pytest.raises(ValueError):
                restored._item_factors.values[0, 0] = 0


def test_cold_empty_no_factors_no_neighbors_and_mixed_scores(bundle, tmp_path):
    engine = RecommendationEngine.from_models([model for model, _, _ in bundle])
    for version in engine.versions():
        response, trace = engine.predict_with_trace(user_id="new", dataset_id=META["dataset_id"], model_version=version)
        assert trace["fallback_reason"] == "cold_start_popular_purchase_count"
        assert all("无用户历史" in item["reason"] for item in response["items"])
    _, trace = engine.predict_with_trace(user_id="view", dataset_id=META["dataset_id"], model_version="bpr_mf_buy_v1")
    assert trace["fallback_reason"] == "popular_no_bpr_positive"
    _, trace = engine.predict_with_trace(user_id="solo", dataset_id=META["dataset_id"], model_version="itemcf_v1")
    assert trace["fallback_reason"] == "popular_no_neighbors"
    response, trace = engine.predict_with_trace(user_id="buy", dataset_id=META["dataset_id"], model_version="itemcf_v1")
    assert trace["item_sources"][0] == "itemcf_cosine"
    assert [item["rank"] for item in response["items"]] == list(range(1, len(response["items"]) + 1))

    empty = PopularModel.fit(frame(), CUTOFF, fit_start=START, **META)
    save_bundle(tmp_path, [(empty, {}, {})])
    loaded = RecommendationEngine.from_registry(tmp_path / "registry.json")
    result, trace = loaded.predict_with_trace(user_id="new", dataset_id=META["dataset_id"], model_version="popular_v1")
    assert result["items"] == [] and trace["fallback_reason"] == "empty_candidate_catalog"


def test_registry_cannot_redirect_outside_directory_or_alias_model(bundle, tmp_path):
    path = save_bundle(tmp_path, bundle)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["models"]["popular_v1"]["file"] = "../outside.asset"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AssetError, match="stay within"):
        RecommendationEngine.from_registry(path)
    path = save_bundle(tmp_path, bundle)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["models"]["pretend_version"] = record["models"].pop("popular_v1")
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AssetError, match="version or source"):
        RecommendationEngine.from_registry(path)


def test_save_rejects_parameters_and_provenance_that_mismatch_model(bundle, tmp_path):
    bpr, params, summary = bundle[2]
    with pytest.raises(ValueError, match="configuration"):
        save_asset(
            bpr,
            tmp_path / "bad.asset",
            parameters={**params, "dimensions": 16},
            provenance={"code_sha256": META["code_sha256"]},
            summary=summary,
        )
    with pytest.raises(ValueError, match="provenance"):
        save_asset(bpr, tmp_path / "bad.asset", parameters=params, provenance={"code_sha256": "wrong"}, summary=summary)


def test_malformed_state_and_semantic_metadata_are_corrupt(bundle, tmp_path):
    import hashlib

    save_bundle(tmp_path, bundle)
    path = tmp_path / "popular_v1.asset"
    original = path.read_bytes()
    for state_change in ("wrong_type", "cutoff_mismatch"):
        path.write_bytes(original)
        with ZipFile(path) as z:
            members = {name: z.read(name) for name in z.namelist()}
        manifest = json.loads(members["manifest.json"])
        state = json.loads(members["popular.json"])
        if state_change == "wrong_type":
            state["event_counts"] = []
        else:
            state["metadata"]["train_cutoff_exclusive"] = "2019-10-03T00:00:00+00:00"
        members["popular.json"] = json.dumps(state).encode()
        manifest["members_sha256"]["popular.json"] = hashlib.sha256(members["popular.json"]).hexdigest()
        members["manifest.json"] = json.dumps(manifest).encode()
        with ZipFile(path, "w", ZIP_DEFLATED) as z:
            for name, content in members.items():
                z.writestr(name, content)
        with pytest.raises(AssetError) as failure:
            load_asset(
                path,
                expected_dataset_id=META["dataset_id"],
                expected_model_version="popular_v1",
                expected_code_sha256=META["code_sha256"],
            )
        assert failure.value.code == "model_corrupt"


def test_incomplete_registry_and_in_memory_identity_conflict_fail(bundle, tmp_path):
    from dataclasses import replace

    path = save_bundle(tmp_path, bundle)
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["models"]["popular_v1"]["code_sha256"]
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AssetError, match="Incomplete"):
        RecommendationEngine.from_registry(path, preload=False)
    model = bundle[2][0]
    conflict = replace(model, metadata=replace(model.metadata, dataset_id="c" * 32))
    with pytest.raises(AssetError, match="fallback identity"):
        RecommendationEngine.from_models([conflict])


def test_empty_batch_still_validates_identity_and_batch_requires_user_list(bundle):
    engine = RecommendationEngine.from_models([model for model, _, _ in bundle])
    request = {"dataset_id": META["dataset_id"], "model_version": "popular_v1"}
    assert engine.predict_batch([], **request) == []
    with pytest.raises(AssetError) as failure:
        engine.predict_batch([], **{**request, "model_version": "unknown"})
    assert failure.value.code == "unknown_model"
    with pytest.raises(AssetError) as failure:
        engine.predict_batch([], **request, limit=0)
    assert failure.value.code == "invalid_limit"
    with pytest.raises(AssetError) as failure:
        engine.predict_batch("three-users", **request)
    assert failure.value.code == "invalid_batch"
