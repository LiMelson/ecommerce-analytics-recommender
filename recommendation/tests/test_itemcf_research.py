from math import log, sqrt

import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from recommendation.benchmark import Labels
from recommendation.itemcf import ItemCFConfig, ItemCFModel
from recommendation.itemcf_research import paired_intervals, validation_stage
from recommendation.popular import PopularModel, local_dataset_id
from recommendation.weighted_itemcf import InteractionFactors, fit_experimental_itemcf, weighted_cosine_neighbors

START = pd.Timestamp("2019-10-01", tz="UTC")
CUTOFF = START + pd.Timedelta(days=14)
CONFIG = ItemCFConfig(neighbors=4, block_rows=2, max_history=100)
WEIGHTS = (1, 5, 8)


def frame(rows):
    events = pd.DataFrame(rows, columns=["user_id", "item_id", "behavior_type", "age"])
    return events.assign(event_time=CUTOFF - pd.to_timedelta(events.pop("age").astype(float), unit="D"), category_id="")


def popular(events):
    return PopularModel.fit(
        events,
        CUTOFF,
        fit_start=START,
        dataset_id=local_dataset_id("a" * 64),
        model_version="popular_v1",
        sample_sha256="a" * 64,
        protocol_version="purchase_global_utc_v1",
        code_sha256="b" * 64,
    )


@pytest.fixture
def events():
    return frame(
        [
            ("u1", "A", "pv", 7),
            ("u1", "A", "pv", 7),
            ("u1", "A", "pv", 7),
            ("u1", "B", "cart", 7),
            ("u2", "A", "buy", 14),
            ("u2", "C", "pv", 7),
            ("u3", "C", "cart", 14),
            ("u3", "D", "pv", 7),
            ("u3", "E", "pv", 7),
        ]
    )


def csr(buffers):
    return csr_matrix((buffers.data, buffers.indices, buffers.indptr), shape=buffers.shape)


@pytest.mark.parametrize(
    ("weights", "half_life", "a_for_u1", "ab", "ac"),
    [
        (None, None, 1, 1 / sqrt(2), 1 / 2),
        (WEIGHTS, None, 2 * log(2), 2 / sqrt(68), 8 / sqrt(68 * 26)),
        (None, 7, 0.5, 0.5 / sqrt(0.3125), 0.4),
        (WEIGHTS, 7, log(2), 1 / sqrt(5), 1 / sqrt(5 * 1.8125)),
    ],
)
def test_four_way_ablation_by_hand(events, weights, half_life, a_for_u1, ab, ac):
    fallback = popular(events)
    if weights is None and half_life is None:
        model, stats = ItemCFModel.fit(events, fallback, CONFIG, model_version="itemcf_v1")
    else:
        model, stats = fit_experimental_itemcf(events, fallback, CONFIG, InteractionFactors(weights, half_life))
    interactions = csr(model._interactions)
    neighbors = csr(model._neighbors)
    assert interactions[0, 0] == pytest.approx(a_for_u1)
    assert stats["interaction_nonzeros"] == 7
    assert neighbors[0, 1] == pytest.approx(ab)
    assert neighbors[0, 2] == pytest.approx(ac)
    assert neighbors.diagonal().sum() == 0
    assert neighbors[0, 3] == 0
    assert neighbors[3, 4] == pytest.approx(1)
    assert "A" not in list(model.rank_ids("u2", 10))
    assert tuple(model.rank_ids("new", 10)) == tuple(fallback.rank_ids("new", 10))


def test_research_window_bounds_future_targets_and_snapshot_validation(events):
    protocol = {"observation_start": START.isoformat(), "observation_end_exclusive": "2019-10-21T00:00:00+00:00"}
    future = frame([("u1", "new_item", "buy", 0), ("u1", "A", "pv", -1), ("u2", "test_item", "buy", -5)])
    combined = pd.concat((events, future), ignore_index=True)
    stage = validation_stage(combined, protocol)
    assert len(stage.fit_events) == len(events)
    assert len(stage.target_events) == 2
    assert stage.cutoff == CUTOFF
    fallback = popular(stage.fit_events)
    factors = InteractionFactors(WEIGHTS, 7)
    model, _ = fit_experimental_itemcf(stage.fit_events, fallback, CONFIG, factors)
    assert "new_item" not in model.items
    assert "test_item" not in model.items
    assert "new_item" in Labels.for_user("u1", frozenset({"new_item"}), model.history).future_items
    with pytest.raises(ValueError, match="fitted history window"):
        fit_experimental_itemcf(combined, fallback, CONFIG, factors)
    changed = stage.fit_events.assign(behavior_type="buy")
    with pytest.raises(ValueError, match="full shared training snapshot"):
        fit_experimental_itemcf(changed, fallback, CONFIG, factors)


def test_same_recent_pair_cap_order_and_finite_scores(events):
    auxiliary = frame([("u1", "A", "fav", 1), ("u1", "E", "remove_from_cart", 1)])
    data = pd.concat((events, auxiliary), ignore_index=True)
    fallback = popular(data)
    factors = InteractionFactors(WEIGHTS, 7)
    config = ItemCFConfig(neighbors=1, block_rows=2, max_history=1)
    model, stats = fit_experimental_itemcf(data, fallback, config, factors)
    shuffled, _ = fit_experimental_itemcf(data.iloc[::-1], fallback, config, factors)
    assert stats["interaction_nonzeros"] == 3
    assert stats["positive_events"] == len(events)
    assert stats["after_history_limit"]["per_user_degree"]["p100"] == 1
    assert model._neighbors == shuffled._neighbors
    assert model._interactions == shuffled._interactions
    assert model._interactions.data[0] == pytest.approx(log(2))
    assert model.history is fallback.history
    assert all(model.recommend(user, 10) == shuffled.recommend(user, 10) for user in ("u1", "u2", "u3"))


def test_sparse_cosine_ties_and_block_bound():
    items = 1_000
    matrix = csr_matrix(np.ones((1, items)))
    graph, stats = weighted_cosine_neighbors(matrix, ItemCFConfig(3, 16))
    assert stats["peak_block_nonzeros"] <= 16 * items
    assert stats["retained_neighbors"] == items * 3
    assert graph.getrow(0).indices.tolist() == [1, 2, 3]
    assert graph.getrow(1).indices.tolist() == [0, 2, 3]
    assert (graph.data == 1).all()


def test_no_valid_positive_behaviors_or_empty_neighbors():
    data = frame([("u1", "A", "remove_from_cart", 7)])
    fallback = popular(data)
    for events in (data, data.iloc[:0]):
        fallback = popular(events)
        model, stats = fit_experimental_itemcf(events, fallback, CONFIG, InteractionFactors(WEIGHTS, 7))
        assert stats["interaction_nonzeros"] == 0
        assert stats["retained_neighbors"] == 0
        assert tuple(model.rank_ids("u1", 10)) == tuple(fallback.rank_ids("u1", 10))


def test_paired_estimate_keeps_same_primary_users_and_seed():
    def labels(items):
        return Labels(frozenset(items), frozenset(items), frozenset(), frozenset())

    targets = {"u1": labels(["A"]), "u2": labels(["B"]), "u3": labels([])}
    before = {"u1": ("X",), "u2": ("B",)}
    after = {"u1": ("A",), "u2": ("B",)}
    bootstrap = {"seed": 17, "resamples": 500, "confidence": 0.95}
    result = paired_intervals(targets, before, after, 1, bootstrap)
    assert result["paired_users"] == 2
    assert result["recall"] == result["ndcg"] == {"mean_delta": 0.5, "ci_lower": 0, "ci_upper": 1}
    assert result == paired_intervals(targets, before, after, 1, bootstrap)
