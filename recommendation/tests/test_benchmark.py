from dataclasses import replace
from math import log2

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from recommendation.benchmark import (
    BenchmarkStage,
    History,
    HistoryBands,
    evaluate,
    ranking_metrics,
    split_events,
    time_boundaries,
)
from recommendation.popular import COLD_FALLBACK, POPULAR, PopularModel, local_dataset_id
from recommendation.run import run_benchmark
from recommendation.sample import read_sample, sha256_file

DIGEST = "a" * 64
START = pd.Timestamp("2019-10-01", tz="UTC")
CUTOFF = pd.Timestamp("2019-10-02", tz="UTC")
END = pd.Timestamp("2019-10-05", tz="UTC")
MODEL_CONFIG = {
    "dataset_id": local_dataset_id(DIGEST),
    "sample_sha256": DIGEST,
    "model_version": "popular_v1",
    "protocol_version": "purchase_global_utc_v1",
    "code_sha256": "b" * 64,
}


def events(*rows):
    frame = pd.DataFrame(rows, columns=["event_time", "user_id", "item_id", "behavior_type", "category_id"])
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, format="ISO8601")
    return frame


def stage_for(fit, target):
    return BenchmarkStage(fit, target, START, CUTOFF, END, len(fit), len(target))


@pytest.fixture
def training():
    return events(
        ("2019-10-01", "001", "0007", "buy", "02"),
        ("2019-10-01", "002", "0007", "buy", "01"),
        ("2019-10-01", "002", "9007199254740993", "buy", "09"),
        ("2019-10-01", "001", "05", "pv", "unknown"),
        ("2019-10-01", "002", "10", "cart", None),
        ("2019-10-01", "9007199254740995", "02", "pv", "01"),
    )


@pytest.fixture
def model(training):
    return PopularModel.fit(training, CUTOFF, fit_start=START, **MODEL_CONFIG)


def test_global_boundaries_and_strict_split():
    boundaries = time_boundaries("2019-10-01T00:00:00Z", "2019-12-01T00:00:00Z")
    assert [stamp.isoformat() for stamp in boundaries] == [
        "2019-10-01T00:00:00+00:00",
        "2019-11-12T16:48:00+00:00",
        "2019-11-21T20:24:00+00:00",
        "2019-12-01T00:00:00+00:00",
    ]
    data = events(
        (boundaries[0].isoformat(), "u", "earliest", "pv", ""),
        ((boundaries[1] - pd.Timedelta(microseconds=1)).isoformat(), "u", "before", "pv", ""),
        (boundaries[1].isoformat(), "u", "at-validation", "buy", ""),
        (boundaries[2].isoformat(), "u", "at-test", "buy", ""),
    )
    train, validation, test = split_events(data, boundaries)
    assert train["item_id"].tolist() == ["earliest", "before"]
    assert validation["item_id"].tolist() == ["at-validation"]
    assert test["item_id"].tolist() == ["at-test"]
    assert set(train.index).isdisjoint(validation.index)
    assert set(train.index).isdisjoint(test.index)
    assert set(validation.index).isdisjoint(test.index)
    with pytest.raises(ValueError, match="outside"):
        split_events(events((boundaries[-1].isoformat(), "u", "end", "buy", "")), boundaries)


def test_training_rejects_future_events(training, model):
    future = events(
        ("2019-10-02", "002", "future", "buy", "99"),
        ("2019-10-03", "001", "0007", "buy", "99"),
    )
    combined = pd.concat([training, future])
    with pytest.raises(ValueError, match="at or after"):
        PopularModel.fit(combined, CUTOFF, **MODEL_CONFIG)
    assert "future" not in model.history.catalog
    assert model.ordered[0].purchase_count == 2
    assert model.ordered[0].category_id == "01"


def test_both_kinds_of_ids_are_kept_as_strings(model):
    assert "9007199254740995" in model.history.event_counts
    assert "001" in model.history.event_counts
    assert "9007199254740993" in model.history.catalog
    assert "0007" in model.history.catalog


def test_popularity_ties_and_fallback_are_reproducible(model, training):
    expected = ["0007", "9007199254740993", "02", "05", "10"]
    assert list(model.rank_ids("unseen", 10)) == expected
    assert list(model.rank_ids("unseen", 2)) == expected[:2]
    assert model.recommend("unseen", 10).source == COLD_FALLBACK
    assert model.recommend("9007199254740995", 10).source == POPULAR
    rebuilt = PopularModel.fit(training.iloc[::-1], CUTOFF, **MODEL_CONFIG)
    assert rebuilt.recommend("unseen", 10) == model.recommend("unseen", 10)


def test_filter_recommendation_is_isolated_per_user_and_topk_is_unique(model):
    first_a = list(model.rank_ids("001", 10))
    result_b = list(model.rank_ids("002", 10))
    assert first_a == ["9007199254740993", "02", "05", "10"]
    assert result_b == ["02", "05", "10"]
    assert len(first_a) == len(set(first_a))
    assert list(model.rank_ids("001", 10)) == first_a


def test_empty_catalog_and_all_items_blocked(training):
    model = PopularModel.fit(training.iloc[:1], CUTOFF, **MODEL_CONFIG)
    request = {"user_id": "001", "dataset_id": MODEL_CONFIG["dataset_id"], "model_version": "popular_v1"}
    assert model.predict(**request)["items"] == []
    empty = PopularModel.fit(training.iloc[:0], CUTOFF, **MODEL_CONFIG)
    assert empty.predict(**{**request, "user_id": "new"})["items"] == []


def test_contract_identity_versions_and_scores(model):
    request = {
        "user_id": "cold",
        "dataset_id": MODEL_CONFIG["dataset_id"],
        "model_version": "popular_v1",
        "limit": 4,
    }
    response = model.predict(**request)
    assert set(response) == {"user_id", "dataset_id", "model_version", "items"}
    assert [response[key] for key in ("user_id", "dataset_id", "model_version")] == [
        request[key] for key in ("user_id", "dataset_id", "model_version")
    ]
    assert [item["rank"] for item in response["items"]] == [1, 2, 3, 4]
    assert response["items"][-1]["category_id"] == ""
    assert response["items"][0]["reason"].startswith("训练期无用户历史")
    assert [item["score"] for item in response["items"]] == [2.0, 1.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="checksum"):
        replace(model.metadata, sample_sha256="c" * 64)
    assert model.metadata.train_cutoff_exclusive == CUTOFF.isoformat()


@pytest.mark.parametrize(
    "attr, invalid",
    [("dataset_id", "wrong"), ("model_version", "wrong"), ("limit", 11), ("user_id", 1)],
)
def test_predict_rejects_invalid_request_fields(model, attr, invalid):
    request = {"user_id": "cold", "dataset_id": MODEL_CONFIG["dataset_id"], "model_version": "popular_v1"}
    with pytest.raises(ValueError):
        model.predict(**{**request, attr: invalid})


def test_metrics_manually_computed_and_duplicate_ranks():
    result = ranking_metrics(["A", "A", "D", "B"], frozenset({"A", "C"}), 10)
    assert result == pytest.approx({"recall": 0.5, "ndcg": 1 / (1 + 1 / log2(3)), "precision": 0.1, "hr": 1})
    result = ranking_metrics(["D", "D", "B", "A"], frozenset({"A", "B", "E"}), 10)
    assert result["ndcg"] == pytest.approx((1 / log2(3) + 1 / log2(4)) / (1 + 1 / log2(3) + 1 / log2(4)))
    assert ranking_metrics(["X"] * 10 + ["A"], frozenset({"A"}), 1)["recall"] == 0
    assert ranking_metrics(["X"], frozenset(), 10) is None
    assert ranking_metrics([], frozenset({"A"}), 10) == {"recall": 0, "ndcg": 0, "precision": 0, "hr": 0}


def test_evaluator_counts_no_purchase_future_products_repeats_and_cold(training, model):
    future = events(
        ("2019-10-02", "001", "0007", "buy", ""),
        ("2019-10-02", "001", "9007199254740993", "buy", ""),
        ("2019-10-02", "001", "9007199254740993", "buy", ""),
        ("2019-10-02", "new", "future", "buy", ""),
        ("2019-10-02", "new", "future", "buy", ""),
        ("2019-10-02", "view-only", "10", "pv", ""),
        ("2019-10-02", "9007199254740995", "10", "pv", ""),
        ("2019-10-03", "9007199254740995", "10", "pv", ""),
        ("2019-10-04", "9007199254740995", "10", "pv", ""),
    )
    bands = HistoryBands.fit(training)
    report = evaluate(stage_for(training, future), model.history, bands, model.rank_ids, 10)
    overall = report["groups"]["overall"]
    assert overall["counts"]["users"] == 4
    assert overall["counts"]["users_with_purchases"] == 2
    assert overall["counts"]["users_without_eligible_targets"] == 3
    assert overall["counts"]["users_without_purchases"] == 2
    assert overall["counts"]["purchase_users_without_eligible_targets"] == 1
    assert overall["counts"]["repeat_purchase_pairs"] == 1
    assert overall["counts"]["eligible_purchase_pairs"] == 1
    assert overall["primary"]["recall"] == 1
    assert overall["all_purchase_targets"]["recall"] == 0.25
    assert report["repeat_purchase_events"] == 1
    assert report["future_items"] == {
        "distinct_items_in_window": 1,
        "distinct_purchased_items": 1,
        "purchase_events": 2,
        "distinct_user_item_purchase_pairs": 1,
        "share_of_purchase_pairs": 1 / 3,
    }
    assert bands == HistoryBands(low_max=1, medium_max=3)
    assert report["groups"]["low"]["counts"]["users"] == 1
    assert report["groups"]["cold"]["counts"]["users"] == 2
    assert report["groups"]["high"]["counts"]["users"] == 0
    assert report["groups"]["high"]["primary"]["ndcg"] is None


def test_evaluator_enforces_shared_candidates_and_counts_empty_rankings(model, training):
    future = events(("2019-10-02", "001", "05", "buy", ""))
    bands = HistoryBands.fit(training)
    stage = stage_for(training, future)
    for invalid in ("unseen", "0007"):
        with pytest.raises(ValueError, match="candidate policy"):
            evaluate(stage, model.history, bands, lambda *_, item=invalid: [item], 10)
    report = evaluate(stage, model.history, bands, lambda *_: [], 10)
    assert report["groups"]["overall"]["primary"]["recall"] == 0
    assert report["groups"]["overall"]["counts"]["effective_users_with_empty_recommendations"] == 1
    assert report["coverage"]["primary_rate"] == 0
    with pytest.raises(ValueError, match="Target window has events outside"):
        evaluate(stage_for(training, training), model.history, bands, model.rank_ids, 10)
    history = History.fit(training.iloc[:0], CUTOFF, fit_start=START)
    report = evaluate(stage_for(training.iloc[:0], future), history, bands, lambda *_: [], 10)
    assert report["coverage"]["primary_rate"] is None
    assert report["groups"]["overall"]["all_purchase_targets"]["ndcg"] == 0


def test_history_mappings_defensively_copy_and_reject_mutation(training, model):
    counts, purchased = {"001": 1}, {"001": {"0007"}}
    history = History(CUTOFF, {"0007"}, counts, purchased, START)
    counts.clear()
    purchased["001"].clear()
    assert history.event_counts == {"001": 1}
    assert history.blocked("001") == {"0007"}
    stage = stage_for(training, events(("2019-10-02", "001", "05", "buy", "")))
    bands = HistoryBands.fit(training)
    before = evaluate(stage, model.history, bands, model.rank_ids, 10)
    with pytest.raises(TypeError):
        model.history.event_counts["001"] = 0
    with pytest.raises(TypeError):
        model.history.purchased["001"] = frozenset()
    with pytest.raises(AttributeError):
        model.history.catalog.add("future")
    assert "0007" not in list(model.rank_ids("001", 10))
    assert evaluate(stage, model.history, bands, model.rank_ids, 10) == before


def test_eval_detects_truncated_windows_out_of_range_events_and_history_mismatch(training, model):
    target = events(("2019-10-02", "001", "05", "buy", ""))
    stage = stage_for(training, target)
    bands = HistoryBands.fit(training)
    for invalid in (replace(stage, fit_events=training.iloc[1:]), replace(stage, target_events=target.iloc[0:0])):
        with pytest.raises(ValueError, match="row count"):
            evaluate(invalid, model.history, bands, model.rank_ids, 10)
    for bad_time in (START, END):
        invalid = replace(stage, target_events=target.assign(event_time=bad_time))
        with pytest.raises(ValueError, match="Target window has events outside"):
            evaluate(invalid, model.history, bands, model.rank_ids, 10)
    with pytest.raises(ValueError, match="strictly increasing"):
        evaluate(replace(stage, target_end=CUTOFF), model.history, bands, model.rank_ids, 10)
    for invalid in (replace(model.history, cutoff=END), replace(model.history, fit_start=START - pd.Timedelta(days=1))):
        with pytest.raises(ValueError, match="History bounds"):
            evaluate(stage, invalid, bands, model.rank_ids, 10)
    with pytest.raises(ValueError, match="History row count"):
        evaluate(stage, History.fit(training.iloc[1:], CUTOFF, fit_start=START), bands, model.rank_ids, 10)


def test_parquet_storage_and_version_checks(tmp_path):
    path = tmp_path / "fixture.parquet"
    table = pa.table(
        {
            "user_id": pa.array(["001", "9007199254740999"]),
            "item_id": pa.array(["9007199254740993", "0007"]),
            "category_id": pa.array(["unknown", "001"]),
            "category_code": pa.array([None, "fruit"]),
            "brand": pa.array(["brand", None]),
            "behavior_type": pa.array(["pv", "buy"]),
            "raw_event_type": pa.array(["view", "purchase"]),
            "event_time": pa.array([0, 1_000_000], type=pa.timestamp("us")),
            "price": pa.array([0.0, 5.0]),
            "session_id": pa.array([None, "session"]),
        }
    )
    pq.write_table(table, path)
    digest = sha256_file(path)
    frame, storage, actual = read_sample(path, {"sample_sha256": digest, "naive_timestamp_timezone": "UTC"})
    assert storage["stored_timestamp_timezone"] is None
    assert storage["types"]["user_id"] == "string"
    assert frame["event_time"].iloc[1].isoformat() == "1970-01-01T00:00:01+00:00"
    assert frame["user_id"].tolist() == ["001", "9007199254740999"]
    assert frame["item_id"].tolist() == ["9007199254740993", "0007"]
    assert actual == digest
    with pytest.raises(ValueError, match="SHA-256"):
        read_sample(path, {"sample_sha256": DIGEST, "naive_timestamp_timezone": "UTC"})
    with pytest.raises(ValueError, match="explicit UTC"):
        read_sample(path, {"sample_sha256": digest, "naive_timestamp_timezone": None})


def test_validation_and_test_use_distinct_cutoffs_with_fixed_bands():
    data = events(
        ("2019-01-01", "a", "p1", "pv", ""),
        ("2019-01-02", "b", "p2", "buy", ""),
        ("2019-01-03", "b", "p2", "pv", ""),
        ("2019-01-15", "val-user", "p3", "buy", ""),
        ("2019-01-18", "test-user", "p3", "buy", ""),
        ("2019-01-19", "test-user", "p4", "buy", ""),
    )
    config = {
        "observation_start": "2019-01-01T00:00:00Z",
        "observation_end_exclusive": "2019-01-21T00:00:00Z",
        "protocol_version": "purchase_global_utc_v1",
        "model_version": "popular_v1",
        "validation_model_version": "popular_v1_validation",
        "top_k": 10,
    }
    result, _ = run_benchmark(data, config, DIGEST, "b" * 64)
    validation, test = result["validation"], result["test"]
    assert validation["model_metadata"]["model_version"] == "popular_v1_validation"
    assert validation["model_metadata"]["train_cutoff_exclusive"] == "2019-01-15T00:00:00+00:00"
    assert validation["future_items"]["distinct_purchased_items"] == 1
    assert validation["groups"]["overall"]["primary"]["users"] == 0
    assert test["model_metadata"]["model_version"] == "popular_v1"
    assert test["model_metadata"]["train_cutoff_exclusive"] == "2019-01-18T00:00:00+00:00"
    assert test["coverage"]["candidate_catalog"] == 3
    assert test["future_items"]["distinct_purchased_items"] == 1
    assert test["groups"]["cold"]["primary"]["recall"] == 1
    assert test["groups"]["cold"]["all_purchase_targets"]["recall"] == 0.5
    assert result["history_bands"]["low_max"] == 1
    assert result["history_bands"]["medium_max"] == 2
    rebuilt, _ = run_benchmark(data.iloc[::-1], config, DIGEST, "b" * 64)
    assert rebuilt == result
