import json
from dataclasses import replace
from math import log
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd
import pytest

from recommendation.benchmark import BenchmarkStage, HistoryBands, evaluate
from recommendation.bpr import (
    BPR,
    FILL,
    INVALID,
    NO_POSITIVE,
    NO_VECTOR,
    BPRConfig,
    BPRModel,
    FrozenFactors,
    pairwise_loss,
    sample_negatives,
)
from recommendation.bpr_experiment import frozen_trial, select_bpr_trial
from recommendation.bpr_io import load_model, save_model
from recommendation.itemcf_experiment import assert_shared_cohort
from recommendation.popular import COLD_FALLBACK, PopularModel, local_dataset_id

START = pd.Timestamp("2019-10-01", tz="UTC")
CUTOFF = pd.Timestamp("2019-10-02", tz="UTC")
END = pd.Timestamp("2019-10-03", tz="UTC")
META = {
    "dataset_id": local_dataset_id("a" * 64),
    "model_version": "popular_v1",
    "sample_sha256": "a" * 64,
    "protocol_version": "purchase_global_utc_v1",
    "code_sha256": "b" * 64,
}


def frame(*rows, time=START):
    data = pd.DataFrame(rows, columns=["user_id", "item_id", "behavior_type"])
    return data.assign(event_time=time, category_id="")


@pytest.fixture
def data():
    return frame(
        ("001", "000A", "buy"),
        ("001", "000A", "buy"),
        ("002", "9007199254740993", "buy"),
        ("003", "000C", "buy"),
        ("view-only", "000D", "pv"),
        ("view-only", "000E", "cart"),
    )


def build(data, config=BPRConfig(epochs=10)):
    fallback = PopularModel.fit(data, CUTOFF, fit_start=START, **META)
    model, training = BPRModel.fit(data, fallback, config, model_version="bpr_mf_buy_v1")
    return model, fallback, training


def test_pairwise_loss_manual_regularization():
    p, q, r = np.array([[1.0, 0.0]]), np.array([[log(3), 0.0]]), np.zeros((1, 2))
    assert pairwise_loss(p, q, r, 0) == pytest.approx(-log(3 / 4))
    assert pairwise_loss(p, q, r, 0.1) == pytest.approx(-log(3 / 4) + 0.05 * (1 + log(3) ** 2))


def test_negative_sampling_excludes_only_training_positives_and_handles_dense_users():
    sets = (frozenset({0, 1}), frozenset(range(99)))
    rows = np.array([0] * 200 + [1] * 200)
    first = sample_negatives(rows, sets, 100, np.random.default_rng(11))
    assert not set(first[:200]) & {0, 1}
    assert set(first[200:]) == {99}
    assert np.array_equal(first, sample_negatives(rows, sets, 100, np.random.default_rng(11)))
    with pytest.raises(ValueError, match="No negative"):
        sample_negatives(np.array([0]), (frozenset({0, 1}),), 2, np.random.default_rng(1))


def test_mapping_dedup_determinism_and_shared_history(data):
    model, fallback, training = build(data)
    shuffled, _, rerun = build(data.sample(frac=1, random_state=9).reset_index(drop=True))
    assert model.users == ("001", "002", "003")
    assert model.items == ("000A", "000C", "000D", "000E", "9007199254740993")
    assert training["purchase_events"] == 4
    assert training["distinct_purchase_pairs"] == training["trainable_positive_pairs"] == 3
    assert training == rerun
    assert np.array_equal(model._user_factors.values, shuffled._user_factors.values)
    assert np.array_equal(model._item_factors.values, shuffled._item_factors.values)
    assert model.history is fallback.history
    with pytest.raises(TypeError):
        model._user_rows["new"] = 1
    with pytest.raises(ValueError):
        model._user_factors.values[0, 0] = 2
    with pytest.raises(TypeError):
        model.history.purchased["001"] = frozenset()
    assert model.fallback.recommend("001", 10) == fallback.recommend("001", 10)


def test_learnable_sample_reduces_fixed_loss_and_forms_preference():
    train = frame(("u1", "A", "buy"), ("u1", "B", "buy"), ("u2", "C", "buy"), ("u2", "D", "buy"), ("u3", "E", "pv"))
    config = BPRConfig(
        dimensions=8,
        epochs=100,
        learning_rate=0.1,
        regularization=0.001,
        batch_size=1,
        negatives_per_positive=5,
        seed=2,
    )
    model, _, stats = build(train, config)
    scores = model._item_factors.values @ model._user_factors.values[0]
    assert min(scores[0], scores[1]) > max(scores[2], scores[3], scores[4])
    assert stats["epochs"][-1]["fixed_diagnostic_loss"] < stats["initial_fixed_diagnostic_loss"] * 0.4


def test_cold_no_positive_untrained_items_purchase_filter_and_source(data):
    model, popular, stats = build(data)
    for user, source in (("new", COLD_FALLBACK), ("view-only", NO_POSITIVE)):
        ranked = model.recommend(user, 10)
        assert ranked.source == source
        assert list(model.rank_ids(user, 10)) == list(popular.rank_ids(user, 10))
        assert all(pick.source == source for pick in ranked.items)
    ranked = model.recommend("001", 10)
    ids = [pick.item_id for pick in ranked.items]
    assert "000A" not in ids and len(ids) == len(set(ids)) == 4
    assert all(pick.item_id in model.trained_items for pick in ranked.items if pick.source == BPR)
    assert all(pick.source == FILL for pick in ranked.items if pick.item_id in {"000D", "000E"})
    assert stats["candidate_items_without_positive_signal"] == 2
    assert len(model._item_factors.values) == len(popular.history.catalog)


def test_stable_ties_invalid_score_and_zero_vectors(data):
    model, _, _ = build(data)
    zero = replace(model, _user_factors=FrozenFactors.from_array(np.zeros(model._user_factors.shape)))
    assert list(zero.rank_ids("001", 2)) == ["000C", "9007199254740993"]
    damaged = replace(model, _user_factors=FrozenFactors.from_array(np.full(model._user_factors.shape, np.nan)))
    assert damaged.recommend("001", 10).source == INVALID


@pytest.mark.parametrize(
    "rows,user,source",
    [
        ((), "new", COLD_FALLBACK),
        ((("u", "A", "pv"),), "u", NO_POSITIVE),
        ((("u", "A", "buy"),), "u", NO_VECTOR),
    ],
)
def test_empty_no_positive_and_saturated_users(rows, user, source):
    model, _, training = build(frame(*rows))
    assert training["fit_status"] == "no_trainable_pairs"
    assert model.recommend(user, 10).source == source
    assert training["epochs"] == []


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("dimensions", 0),
        ("epochs", True),
        ("learning_rate", float("inf")),
        ("regularization", -1),
        ("batch_size", 0),
        ("negatives_per_positive", 0),
        ("seed", -1),
    ],
)
def test_invalid_config(keyword, value):
    with pytest.raises(ValueError):
        BPRConfig(**{keyword: value})


def test_rejects_future_and_modified_same_size_snapshot(data):
    _, fallback, _ = build(data)
    for bad in (data.assign(event_time=CUTOFF), data.assign(event_time=START - pd.Timedelta(days=1))):
        with pytest.raises(ValueError, match="outside"):
            BPRModel.fit(bad, fallback, BPRConfig(), model_version="x")
    bad = data.copy()
    bad.loc[0, "user_id"] = "changed"
    with pytest.raises(ValueError, match="snapshot"):
        BPRModel.fit(bad, fallback, BPRConfig(), model_version="x")


def test_save_load_rankings_and_version_rejection(data, tmp_path):
    model, popular, training = build(data)
    path = tmp_path / "model.bpr"
    save_model(
        model, path, provenance={"code_sha256": META["code_sha256"]}, training_summary=training, evaluation={"users": 1}
    )
    loaded = load_model(path, popular, model_version=model.metadata.model_version, code_sha256=META["code_sha256"])
    for user in ("001", "002", "003", "view-only", "new"):
        assert model.recommend(user, 10) == loaded.recommend(user, 10)
    for name, value in (("model_version", "wrong"), ("code_sha256", "wrong")):
        params = {"model_version": model.metadata.model_version, "code_sha256": META["code_sha256"], name: value}
        with pytest.raises(ValueError, match="identity"):
            load_model(path, popular, **params)
    with pytest.raises(ValueError, match="snapshot"):
        load_model(
            path,
            replace(popular, training_snapshot_sha256="wrong"),
            model_version=model.metadata.model_version,
            code_sha256=META["code_sha256"],
        )
    with ZipFile(path) as z:
        members = {n: z.read(n) for n in z.namelist()}
    record = json.loads(members["metadata.json"])
    record["format"] = "unknown"
    members["metadata.json"] = json.dumps(record).encode()
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    with pytest.raises(ValueError, match="format"):
        load_model(path, popular, model_version=model.metadata.model_version, code_sha256=META["code_sha256"])


def test_common_benchmark_keeps_no_positive_users_and_future_targets(data):
    model, popular, _ = build(data)
    future = frame(
        ("view-only", "000C", "buy"),
        ("new", "future", "buy"),
        ("001", "9007199254740993", "buy"),
        ("001", "000A", "buy"),
        time=CUTOFF,
    )
    stage = BenchmarkStage(data, future, START, CUTOFF, END, len(data), len(future))
    bands = HistoryBands.fit(data)
    actual = evaluate(stage, model.history, bands, model.rank_ids, 10)
    baseline = evaluate(stage, popular.history, bands, popular.rank_ids, 10)
    for score in (actual, baseline):
        score["users_known_in_initial_train"] = 2
    assert_shared_cohort(actual, baseline)
    assert actual["groups"]["overall"]["primary"]["users"] == 2
    assert actual["groups"]["overall"]["all_purchase_targets"]["users"] == 3
    assert actual["future_items"]["distinct_purchased_items"] == 1


def test_validation_selection_and_source_gate():
    def trial(name, ndcg):
        return {
            "id": name,
            "parameters": {"dimensions": 16, "epochs": 40},
            "status": "succeeded",
            "training": {"fit_status": "trained"},
            "evaluation": {"groups": {"overall": {"primary": {"ndcg": ndcg, "recall": 0.2}}}},
        }

    trials = [trial("a", 0.1), trial("b", 0.2), {"id": "c", "parameters": {}, "status": "failed"}]
    assert select_bpr_trial(trials)["id"] == "b"
    with pytest.raises(ValueError, match="usable"):
        select_bpr_trial(trials[-1:])
    report = {"study": {"trials": []}, "protocol": {}, "provenance": {"code_sha256": "old"}}
    with pytest.raises(ValueError, match="incompatible"):
        frozen_trial(report, report["study"], {}, {"code_sha256": "new"})


def test_training_negatives_use_only_past_catalog_and_do_not_mutate_itemcf(data, monkeypatch):
    import recommendation.bpr as module
    from recommendation.itemcf import ItemCFConfig, ItemCFModel

    _, popular, _ = build(data)
    itemcf, _ = ItemCFModel.fit(data, popular, ItemCFConfig(3, 2), model_version="itemcf_v1")
    before = itemcf.recommend("001", 10)
    captured = []
    original = module.sample_negatives

    def checked(rows, positives, item_count, rng):
        captured.append((item_count, positives))
        assert item_count == len(popular.history.catalog)
        return original(rows, positives, item_count, rng)

    monkeypatch.setattr(module, "sample_negatives", checked)
    BPRModel.fit(data, popular, BPRConfig(epochs=2), model_version="bpr_mf_buy_v1")
    assert captured
    assert itemcf.recommend("001", 10) == before
    assert "future-only" not in popular.history.catalog
    future = frame(("001", "future-only", "buy"), time=CUTOFF)
    with pytest.raises(ValueError, match="outside"):
        BPRModel.fit(pd.concat((data, future)), popular, BPRConfig(epochs=2), model_version="bpr_mf_buy_v1")


def test_loaded_artifact_rejects_other_dataset_and_corrupted_arrays(data, tmp_path):
    model, popular, training = build(data)
    path = tmp_path / "model.bpr"
    save_model(model, path, provenance={"code_sha256": META["code_sha256"]}, training_summary=training, evaluation={})
    changed = replace(popular, metadata=replace(popular.metadata, dataset_id="c" * 32))
    with pytest.raises(ValueError, match="dataset_id"):
        load_model(path, changed, model_version=model.metadata.model_version, code_sha256=META["code_sha256"])
    with ZipFile(path) as z:
        members = {n: z.read(n) for n in z.namelist()}
    members["item_factors.npy"] = b"corrupted"
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    with pytest.raises(ValueError, match="checksum"):
        load_model(path, popular, model_version=model.metadata.model_version, code_sha256=META["code_sha256"])
