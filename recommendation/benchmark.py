"""Shared full-catalog benchmark for future purchases."""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import pairwise
from math import ceil, fsum, log2

import pandas as pd

SEGMENTS = ("cold", "low", "medium", "high")
METRICS = ("recall", "ndcg", "precision", "hr")


def time_boundaries(start, end):
    """Return UTC boundaries at 0%, 70%, 85%, and 100% of elapsed time."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tz is None or end.tz is None or start >= end:
        raise ValueError("Observation bounds must be ordered and timezone-aware")
    start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
    duration = end - start
    return start, start + duration * 70 // 100, start + duration * 85 // 100, end


def split_events(events, boundaries):
    times = events["event_time"]
    if not ((times >= boundaries[0]) & (times < boundaries[-1])).all():
        raise ValueError("Missing timestamps or events outside the observation window")
    return tuple(events.loc[(times >= start) & (times < end)] for start, end in pairwise(boundaries))


def nearest_rank(values, percentile):
    """Nearest-rank quantile of a sorted, nonempty sequence."""
    return values[ceil(percentile * len(values)) - 1]


@dataclass(frozen=True)
class HistoryBands:
    low_max: int
    medium_max: int

    @classmethod
    def fit(cls, train):
        counts = sorted(train.groupby("user_id").size().tolist())
        if not counts:
            raise ValueError("Cannot fit history bands without training events")
        return cls(nearest_rank(counts, 0.25), nearest_rank(counts, 0.75))

    def segment(self, count):
        if count == 0:
            return "cold"
        if count <= self.low_max:
            return "low"
        if count <= self.medium_max:
            return "medium"
        return "high"


def _purchase_sets(events):
    purchases = events.loc[events["behavior_type"].eq("buy"), ["user_id", "item_id"]]
    return {user_id: frozenset(items) for user_id, items in purchases.groupby("user_id", sort=False)["item_id"]}


@dataclass(frozen=True)
class History:
    cutoff: pd.Timestamp
    catalog: frozenset[str]
    event_counts: dict[str, int]
    purchased: dict[str, frozenset[str]]

    @classmethod
    def fit(cls, events, cutoff):
        if not events["event_time"].lt(cutoff).all():
            raise ValueError("Training history includes an event at or after the cutoff")
        return cls(
            cutoff,
            frozenset(events["item_id"]),
            events["user_id"].value_counts().to_dict(),
            _purchase_sets(events),
        )

    def blocked(self, user_id):
        return self.purchased.get(user_id, frozenset())


def window_summary(window, start, end, initial_train, history):
    users, items = set(window["user_id"]), set(window["item_id"])
    purchases = window.loc[window["behavior_type"].eq("buy")]
    return {
        "start_inclusive": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "events": len(window),
        "users": len(users),
        "items": len(items),
        "purchase_events": len(purchases),
        "purchase_users": purchases["user_id"].nunique(),
        "first_seen_users": len(users - history.event_counts.keys()),
        "known_items_at_window_start": len(history.catalog),
        "items_in_initial_train": len(items & initial_train.catalog),
        "items_known_at_window_start": len(items & history.catalog),
        "items_first_seen_in_window": len(items - history.catalog),
        "events_by_type": window["behavior_type"].value_counts().to_dict(),
    }


@dataclass(frozen=True)
class Labels:
    observed: frozenset[str]
    eligible: frozenset[str]
    future_items: frozenset[str]
    repeat_items: frozenset[str]

    @classmethod
    def for_user(cls, user_id, observed, history):
        future_items = observed - history.catalog
        repeat_items = observed & history.blocked(user_id)
        return cls(observed, observed - future_items - repeat_items, future_items, repeat_items)


def ranking_metrics(ranking: Iterable[str], targets: frozenset[str], k: int):
    """Return binary metrics for one user, or None when that user has no target."""
    if k < 1:
        raise ValueError("k must be positive")
    if not targets:
        return None
    # Keep the first occurrence, then truncate to K.
    ranking = tuple(dict.fromkeys(ranking))[:k]
    hit_ranks = [rank for rank, item in enumerate(ranking, 1) if item in targets]
    dcg = fsum(1 / log2(rank + 1) for rank in hit_ranks)
    idcg = fsum(1 / log2(rank + 1) for rank in range(1, min(k, len(targets)) + 1))
    hits = len(hit_ranks)
    return dict(zip(METRICS, (hits / len(targets), dcg / idcg, hits / k, float(hits > 0)), strict=True))


def _macro(scores):
    return {
        "users": len(scores),
        **{name: fsum(row[name] for row in scores) / len(scores) if scores else None for name in METRICS},
    }


def _user_counts(labels, ranking, k):
    return {
        "users": 1,
        "users_with_purchases": bool(labels.observed),
        "users_with_eligible_targets": bool(labels.eligible),
        "users_with_future_item_purchases": bool(labels.future_items),
        "users_with_repeat_purchases": bool(labels.repeat_items),
        "users_without_eligible_targets": not labels.eligible,
        "users_without_purchases": not labels.observed,
        "purchase_users_without_eligible_targets": bool(labels.observed) and not labels.eligible,
        "distinct_user_item_purchase_pairs": len(labels.observed),
        "eligible_purchase_pairs": len(labels.eligible),
        "future_item_purchase_pairs": len(labels.future_items),
        "repeat_purchase_pairs": len(labels.repeat_items),
        "users_with_empty_recommendations": not ranking,
        "users_with_less_than_k_recommendations": len(ranking) < k,
        "effective_users_with_empty_recommendations": bool(labels.eligible) and not ranking,
    }


def evaluate(
    window,
    history: History,
    bands: HistoryBands,
    rank: Callable[[str, int], Iterable[str]],
    k: int,
):
    """Evaluate every window user with one fixed history, candidate policy and K."""
    if k < 1:
        raise ValueError("k must be positive")
    if not window["event_time"].ge(history.cutoff).all():
        raise ValueError("Evaluation window overlaps training history")
    groups = ("overall", *SEGMENTS)
    empty_labels = Labels(frozenset(), frozenset(), frozenset(), frozenset())
    zero_counts = dict.fromkeys(_user_counts(empty_labels, (), k), 0)
    counts = {group: Counter(zero_counts) for group in groups}
    primary = {group: [] for group in groups}
    all_purchases = {group: [] for group in groups}
    primary_coverage, all_coverage = set(), set()
    users = sorted(window["user_id"].unique())
    observed = _purchase_sets(window)
    for user_id in users:
        labels = Labels.for_user(user_id, observed.get(user_id, empty_labels.observed), history)
        ranking = tuple(dict.fromkeys(rank(user_id, k)))[:k]
        blocked = history.blocked(user_id)
        if any(item not in history.catalog or item in blocked for item in ranking):
            raise ValueError("Ranker returned an item outside the shared candidate policy")
        segment = bands.segment(history.event_counts.get(user_id, 0))
        eligible_scores = ranking_metrics(ranking, labels.eligible, k)
        observed_scores = ranking_metrics(ranking, labels.observed, k)
        user_counts = _user_counts(labels, ranking, k)
        for group in ("overall", segment):
            counts[group].update(user_counts)
            if eligible_scores is not None:
                primary[group].append(eligible_scores)
            if observed_scores is not None:
                all_purchases[group].append(observed_scores)
        all_coverage.update(ranking)
        if eligible_scores is not None:
            primary_coverage.update(ranking)

    purchase_events = window.loc[window["behavior_type"].eq("buy")]
    future_purchases = purchase_events.loc[~purchase_events["item_id"].isin(history.catalog)]
    repeat_events = sum(
        item_id in history.blocked(user_id)
        for user_id, item_id in purchase_events[["user_id", "item_id"]].itertuples(index=False, name=None)
    )
    future_pairs = counts["overall"]["future_item_purchase_pairs"]
    observed_pairs = counts["overall"]["distinct_user_item_purchase_pairs"]
    return {
        "k": k,
        "groups": {
            group: {
                "counts": dict(counts[group]),
                "primary": _macro(primary[group]),
                "all_purchase_targets": _macro(all_purchases[group]),
            }
            for group in groups
        },
        "future_items": {
            "distinct_items_in_window": len(set(window["item_id"]) - history.catalog),
            "distinct_purchased_items": future_purchases["item_id"].nunique(),
            "purchase_events": len(future_purchases),
            "distinct_user_item_purchase_pairs": future_pairs,
            "share_of_purchase_pairs": future_pairs / observed_pairs if observed_pairs else None,
        },
        "repeat_purchase_events": repeat_events,
        "coverage": {
            "candidate_catalog": len(history.catalog),
            "primary_distinct_items": len(primary_coverage),
            "primary_rate": len(primary_coverage) / len(history.catalog) if history.catalog else None,
            "all_window_users_distinct_items": len(all_coverage),
            "all_window_users_rate": len(all_coverage) / len(history.catalog) if history.catalog else None,
        },
    }
