from dataclasses import FrozenInstanceError
from math import sqrt

import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from recommendation.benchmark import BenchmarkStage, HistoryBands, evaluate
from recommendation.itemcf import FILL, ITEMCF, MIXED, NO_NEIGHBORS, ItemCFConfig, ItemCFModel, cosine_neighbors
from recommendation.itemcf_experiment import assert_shared_cohort, select_trial, verify_selection
from recommendation.popular import COLD_FALLBACK, PopularModel, local_dataset_id

START = pd.Timestamp("2019-10-01", tz="UTC")
CUTOFF = pd.Timestamp("2019-10-02", tz="UTC")
END = pd.Timestamp("2019-10-03", tz="UTC")
DIGEST = "a" * 64
METADATA = {
    "dataset_id": local_dataset_id(DIGEST),
    "model_version": "popular_v1",
    "sample_sha256": DIGEST,
    "protocol_version": "purchase_global_utc_v1",
    "code_sha256": "b" * 64,
}


def frame(*rows, timestamp=START):
    result = pd.DataFrame(rows, columns=["user_id", "item_id", "behavior_type"])
    return result.assign(event_time=timestamp, category_id="")


def models(data, config=ItemCFConfig(5, 2)):
    fallback = PopularModel.fit(data, CUTOFF, fit_start=START, **METADATA)
    model, stats = ItemCFModel.fit(data, fallback, config, model_version="itemcf_v1")
    return model, fallback, stats


@pytest.fixture
def toy():
    return frame(
        ("甲", "A", "buy"),
        ("甲", "B", "pv"),
        ("甲", "B", "pv"),
        ("乙", "A", "fav"),
        ("乙", "C", "cart"),
        ("丙", "C", "pv"),
        ("丙", "D", "pv"),
        ("丙", "E", "pv"),
        ("甲", "E", "remove_from_cart"),
    )


def test_hand_calculated_cosines_and_binary_interactions(toy):
    model, _, stats = models(toy)
    frozen = model._neighbors
    graph = csr_matrix((frozen.data, frozen.indices, frozen.indptr), shape=frozen.shape)
    expected = {
        ("A", "B"): 1 / sqrt(2),
        ("A", "C"): 1 / 2,
        ("C", "D"): 1 / sqrt(2),
        ("C", "E"): 1 / sqrt(2),
        ("D", "E"): 1,
    }
    for (a, b), score in expected.items():
        i, j = model.items.index(a), model.items.index(b)
        assert float(graph[i, j]) == pytest.approx(score)
        assert float(graph[j, i]) == pytest.approx(score)
    assert graph[model.items.index("A"), model.items.index("E")] == 0
    assert graph.diagonal().sum() == 0
    assert stats["positive_events"] == 8
    assert stats["interaction_nonzeros"] == 7
    assert stats["before_history_limit"]["unordered_cooccurrence_contributions"] == 5


def test_personalized_scores_ties_purchase_filter_and_repeatability(toy):
    model, popular, _ = models(toy)
    a = model.recommend("甲", 2)
    assert [pick.item_id for pick in a.items] == ["B", "C"]
    assert [pick.score for pick in a.items] == pytest.approx([1 / sqrt(2), 0.5])
    assert [pick.item_id for pick in model.recommend("乙", 3).items] == ["B", "D", "E"]
    assert list(model.rank_ids("乙", 3)) != list(popular.rank_ids("乙", 3))
    assert model.recommend("甲", 2) == a
    rebuilt, _, _ = models(toy.iloc[::-1])
    assert rebuilt.recommend("甲", 2) == a
    assert rebuilt.recommend("乙", 10) == model.recommend("乙", 10)
    assert a.source == ITEMCF


def test_shortfalls_and_cold_start_use_filtered_popular_without_duplicates(toy):
    additions = frame(*(("other", f"p{i:02d}", "buy") for i in range(12)), ("aux", "aux-item", "remove_from_cart"))
    model, popular, _ = models(pd.concat((toy, additions)))
    ranking = model.recommend("甲", 10)
    ids = [pick.item_id for pick in ranking.items]
    assert len(ids) == len(set(ids)) == 10
    assert ids[:2] == ["B", "C"] and "A" not in ids
    assert ranking.source == MIXED
    assert [pick.source for pick in ranking.items] == [ITEMCF, ITEMCF] + [FILL] * 8
    cold = model.recommend("new", 10)
    assert cold.source == COLD_FALLBACK
    assert all(pick.source == COLD_FALLBACK for pick in cold.items)
    assert list(model.rank_ids("new", 10)) == list(popular.rank_ids("new", 10))
    assert model.recommend("aux", 10).source == NO_NEIGHBORS


@pytest.mark.parametrize(
    "rows,user,expected",
    [
        ((), "unknown", []),
        ((("seen", "0001", "pv"),), "seen", ["0001"]),
        ((("seen", "0001", "buy"),), "seen", []),
        ((("one", "0001", "buy"), ("two", "9007199254740993", "pv")), "one", ["9007199254740993"]),
    ],
)
def test_empty_single_item_and_no_similarity(rows, user, expected):
    model, _, stats = models(frame(*rows))
    assert list(model.rank_ids(user, 10)) == expected
    assert stats["retained_neighbors"] == 0
    assert model.recommend(user, 10).source == (NO_NEIGHBORS if rows else COLD_FALLBACK)


def test_refuses_future_and_incomplete_training_without_mutating_history(toy):
    model, popular, _ = models(toy)
    original = model.history
    assert original is popular.history
    with pytest.raises(TypeError):
        original.purchased["甲"] = frozenset()
    with pytest.raises(ValueError, match="outside"):
        ItemCFModel.fit(toy.assign(event_time=CUTOFF), popular, ItemCFConfig(5, 2), model_version="itemcf_v1")
    with pytest.raises(ValueError, match="outside"):
        ItemCFModel.fit(
            toy.assign(event_time=START - pd.Timedelta(days=1)), popular, ItemCFConfig(5, 2), model_version="itemcf_v1"
        )
    with pytest.raises(ValueError, match="full shared history"):
        ItemCFModel.fit(toy.iloc[:-1], popular, ItemCFConfig(5, 2), model_version="itemcf_v1")
    assert original.blocked("甲") == {"A"}
    assert "A" not in list(model.rank_ids("甲", 10))


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("user_id", "new-user"),
        ("user_id", "乙"),
        ("item_id", "B"),
        ("behavior_type", "cart"),
        ("event_time", START + pd.Timedelta(minutes=5)),
        ("category_id", "different-category"),
    ],
)
def test_rejects_same_size_substituted_snapshot(toy, field, replacement):
    _, popular, _ = models(toy)
    altered = toy.copy()
    altered.loc[0, field] = replacement
    with pytest.raises(ValueError, match="training snapshot content does not match"):
        ItemCFModel.fit(altered, popular, ItemCFConfig(5, 2), model_version="itemcf_v1")


def test_reordered_snapshot_with_duplicates_and_nulls_is_equivalent(toy):
    data = toy.assign(brand=None, price=[None, 0.0, -0.0, 4.0, None, 1.0, 2.0, 3.0, None])
    model, popular, _ = models(data)
    shuffled = data.sample(frac=1, random_state=19).reset_index(drop=True).loc[:, data.columns[::-1]]
    rebuilt, _ = ItemCFModel.fit(shuffled, popular, ItemCFConfig(5, 2), model_version="itemcf_v1")
    assert rebuilt.recommend("甲", 10) == model.recommend("甲", 10)


def test_fitted_snapshot_detects_mutation_of_original_frame(toy):
    _, popular, _ = models(toy)
    toy.loc[0, "behavior_type"] = "cart"
    with pytest.raises(ValueError, match="training snapshot content does not match"):
        ItemCFModel.fit(toy, popular, ItemCFConfig(5, 2), model_version="itemcf_v1")


def test_fitted_state_blocks_external_mutation_without_changing_rankings(toy):
    model, _, _ = models(toy)
    before = model.recommend("甲", 10)
    with pytest.raises(TypeError):
        model._user_rows["甲"] = model._user_rows["乙"]
    for matrix in (model._interactions, model._neighbors):
        for buffer in (matrix.data, matrix.indices, matrix.indptr):
            with pytest.raises(ValueError, match="read-only"):
                buffer[0] = 0
            with pytest.raises(ValueError):
                buffer.setflags(write=True)
        with pytest.raises(FrozenInstanceError):
            matrix.indices = matrix.indices.copy()
        assert matrix.indices is not matrix.indices
    assert model.recommend("甲", 10) == before


def test_mixed_ranking_keeps_itemcf_before_raw_higher_score_fallback(toy):
    noisy = pd.concat((toy, frame(("other", "popular", "buy"), ("other", "popular", "buy"))))
    model, _, _ = models(noisy)
    ranking = model.recommend("甲", 3)
    assert [(pick.item_id, pick.source) for pick in ranking.items] == [
        ("B", ITEMCF),
        ("C", ITEMCF),
        ("popular", FILL),
    ]
    assert ranking.items[2].score > ranking.items[0].score


def test_history_cap_uses_most_recent_unique_items_without_shrinking_policy():
    previous = frame(("u", "a", "buy"), ("u", "b", "pv"), ("u", "c", "pv"))
    recent = frame(("u", "b", "cart"), ("u", "a", "pv"), timestamp=START + pd.Timedelta(hours=1))
    data = pd.concat((previous, recent))
    model, _, stats = models(data, ItemCFConfig(2, 1, max_history=1))
    assert model.history.catalog == {"a", "b", "c"}
    assert model.history.blocked("u") == {"a"}
    assert model._interactions.indices.tolist() == [0]
    assert stats["before_history_limit"]["unique_pairs"] == 3
    assert stats["after_history_limit"]["unique_pairs"] == 1
    assert stats["users_truncated"] == 1


def test_high_degree_multiplication_is_bounded_and_sparse(monkeypatch):
    n_items, block_rows, n_neighbors = 600, 32, 4
    interactions = csr_matrix(
        (np.ones(n_items, dtype=np.int32), ([0] * n_items, np.arange(n_items))), shape=(1, n_items)
    )
    original = csr_matrix.__matmul__
    shapes = []

    def bounded_multiply(left, right):
        assert isinstance(right, csr_matrix)
        assert left.shape[0] <= block_rows
        shapes.append(left.shape)
        return original(left, right)

    monkeypatch.setattr(csr_matrix, "__matmul__", bounded_multiply)
    graph, stats = cosine_neighbors(interactions, ItemCFConfig(n_neighbors, block_rows))
    assert len(shapes) == 19
    assert isinstance(graph, csr_matrix)
    assert graph.nnz == n_items * n_neighbors
    assert stats["peak_block_nonzeros"] == block_rows * n_items
    assert stats["directed_similarity_pairs_before_top_n"] == n_items * (n_items - 1)


def test_models_share_same_benchmark_users_labels_and_metric_calculators(toy):
    model, popular, _ = models(toy)
    future = frame(
        ("甲", "A", "buy"),
        ("甲", "B", "buy"),
        ("乙", "D", "buy"),
        ("new", "unknown", "buy"),
        ("cold", "C", "buy"),
        timestamp=CUTOFF,
    )
    stage = BenchmarkStage(toy, future, START, CUTOFF, END, len(toy), len(future))
    bands = HistoryBands.fit(toy)

    def score(ranker):
        return {
            **evaluate(stage, model.history, bands, ranker, 2),
            "users_known_in_initial_train": 2,
        }

    evaluation, baseline = score(model.rank_ids), score(popular.rank_ids)
    assert_shared_cohort(evaluation, baseline)
    assert evaluation["groups"]["overall"]["primary"]["users"] == 3
    assert evaluation["groups"]["overall"]["all_purchase_targets"]["users"] == 4
    assert evaluation["groups"]["cold"]["primary"]["users"] == 1
    assert evaluation["groups"]["overall"]["primary"]["ndcg"] > baseline["groups"]["overall"]["primary"]["ndcg"]
    baseline["groups"]["overall"]["counts"]["eligible_purchase_pairs"] += 1
    with pytest.raises(ValueError, match="cohort"):
        assert_shared_cohort(evaluation, baseline)


def test_select_validation_only_and_reject_modified_configuration():
    def trial(name, neighbors, recall, ndcg):
        return {
            "id": name,
            "parameters": {"neighbors": neighbors, "max_history": None, "block_rows": 32},
            "evaluation": {"groups": {"overall": {"primary": {"recall": recall, "ndcg": ndcg}}}},
        }

    trials = [trial("a", 30, 0.6, 0.4), trial("b", 80, 0.5, 0.5), trial("c", 50, 0.5, 0.5)]
    assert select_trial(trials)["id"] == "c"
    study = {"trials": [{"id": t["id"], **t["parameters"]} for t in trials]}
    report = {
        "study": study,
        "protocol": {"top_k": 10},
        "provenance": {"code_sha256": "abc"},
        "trials": trials,
        "selected_trial": "c",
    }
    assert verify_selection(report, study, {"top_k": 10}, {"code_sha256": "abc"})["id"] == "c"
    with pytest.raises(ValueError, match="current inputs"):
        verify_selection(report, study, {"top_k": 10}, {"code_sha256": "new"})
    with pytest.raises(ValueError, match="frozen"):
        verify_selection({**report, "selected_trial": "b"}, study, {"top_k": 10}, {"code_sha256": "abc"})
