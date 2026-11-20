"""Deterministic CPU BPR-MF with binary purchase positives and sampled negatives."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from math import isfinite
from types import MappingProxyType

import numpy as np
from scipy.special import expit

from .popular import COLD_FALLBACK, ModelMetadata, PopularModel, Ranking
from .training_snapshot import training_snapshot_sha256

BPR = "bpr_mf"
NO_POSITIVE = "popular_no_bpr_positive"
NO_VECTOR = "popular_no_trainable_vector"
INVALID = "popular_invalid_bpr_score"
FILL = "popular_bpr_fill"


@dataclass(frozen=True)
class BPRConfig:
    dimensions: int = 16
    epochs: int = 40
    learning_rate: float = 0.05
    regularization: float = 0.01
    batch_size: int = 128
    negatives_per_positive: int = 3
    seed: int = 20261120

    def __post_init__(self):
        for name, value, upper in (
            ("dimensions", self.dimensions, 128),
            ("epochs", self.epochs, 500),
            ("batch_size", self.batch_size, 4096),
            ("negatives_per_positive", self.negatives_per_positive, 20),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"{name} must be an integer between 1 and {upper}")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be an integer in [0, 2**32)")
        if not isfinite(self.learning_rate) or not 0 < self.learning_rate <= 1:
            raise ValueError("learning_rate must be finite and in (0, 1]")
        if not isfinite(self.regularization) or not 0 <= self.regularization <= 1:
            raise ValueError("regularization must be finite and in [0, 1]")


def pairwise_loss(user_vectors, positive_vectors, negative_vectors, regularization):
    difference = np.einsum("ij,ij->i", user_vectors, positive_vectors - negative_vectors, optimize=False)
    penalty = (
        regularization
        / 2
        * (
            np.einsum("ij,ij->i", user_vectors, user_vectors, optimize=False)
            + np.einsum("ij,ij->i", positive_vectors, positive_vectors, optimize=False)
            + np.einsum("ij,ij->i", negative_vectors, negative_vectors, optimize=False)
        )
    )
    return float(np.mean(np.logaddexp(0, -difference) + penalty))


def sample_negatives(user_rows, positives, item_count, rng):
    """Uniformly sample catalog minus training positives; use bounded rejection."""
    if item_count < 1 or any(len(positives[int(row)]) >= item_count for row in set(user_rows)):
        raise ValueError("No negative candidate exists for at least one training user")
    sampled = rng.integers(item_count, size=len(user_rows), dtype=np.int64)
    for _ in range(8):
        bad = np.fromiter(
            (int(item) in positives[int(user)] for user, item in zip(user_rows, sampled, strict=True)),
            dtype=bool,
            count=len(user_rows),
        )
        if not bad.any():
            return sampled
        sampled[bad] = rng.integers(item_count, size=int(bad.sum()))
    # Conditional uniform sampling from the explicit complement handles dense users.
    for user in sorted(set(user_rows[bad])):
        positions = np.flatnonzero(bad & (user_rows == user))
        allowed = np.array(sorted(set(range(item_count)) - positives[int(user)]), dtype=np.int64)
        sampled[positions] = rng.choice(allowed, size=len(positions))
    return sampled


@dataclass(frozen=True)
class FrozenFactors:
    shape: tuple[int, int]
    content: bytes

    @classmethod
    def from_array(cls, values):
        return cls(values.shape, np.ascontiguousarray(values, dtype=np.float64).tobytes())

    @property
    def values(self):
        return np.frombuffer(self.content, dtype=np.float64).reshape(self.shape)


@dataclass(frozen=True)
class BPRPick:
    item_id: str
    score: float
    source: str


@dataclass(frozen=True)
class BPRModel:
    fallback: PopularModel
    metadata: ModelMetadata
    config: BPRConfig
    users: tuple[str, ...]
    items: tuple[str, ...]
    trained_items: frozenset[str]
    _user_rows: Mapping[str, int]
    _user_factors: FrozenFactors
    _item_factors: FrozenFactors

    @property
    def history(self):
        return self.fallback.history

    @classmethod
    def fit(cls, events, fallback, config, *, model_version):
        times = events["event_time"]
        history = fallback.history
        if not times.lt(history.cutoff).all() or (
            history.fit_start is not None and not times.ge(history.fit_start).all()
        ):
            raise ValueError("BPR training events are outside the history window")
        if training_snapshot_sha256(events) != fallback.training_snapshot_sha256:
            raise ValueError("BPR training snapshot content does not match the fitted Popular model")

        items = tuple(sorted(history.catalog))
        item_rows = {item: index for index, item in enumerate(items)}
        positives = {
            user: frozenset(item_rows[item] for item in purchased)
            for user, purchased in sorted(history.purchased.items())
        }
        users = tuple(user for user, purchased in positives.items() if 0 < len(purchased) < len(items))
        user_rows = {user: row for row, user in enumerate(users)}
        train_sets = tuple(positives[user] for user in users)
        pairs = [(row, item) for row, purchased in enumerate(train_sets) for item in sorted(purchased)]
        pair_array = np.array(pairs, dtype=np.int64).reshape(-1, 2)
        rng = np.random.default_rng(config.seed)
        user_factors = rng.normal(0, 0.1, (len(users), config.dimensions))
        item_factors = rng.normal(0, 0.1, (len(items), config.dimensions))
        signal_items = frozenset(items[item] for _, item in pairs)
        logs = []
        if pairs:
            diagnostic_rng = np.random.default_rng(config.seed + 1)
            diagnostic_negatives = sample_negatives(pair_array[:, 0], train_sets, len(items), diagnostic_rng)
            initial_loss = pairwise_loss(
                user_factors[pair_array[:, 0]],
                item_factors[pair_array[:, 1]],
                item_factors[diagnostic_negatives],
                config.regularization,
            )
            for epoch in range(config.epochs):
                order = rng.permutation(len(pairs) * config.negatives_per_positive)
                expanded = pair_array[order // config.negatives_per_positive]
                negatives = sample_negatives(expanded[:, 0], train_sets, len(items), rng)
                sampled_losses = []
                for start in range(0, len(expanded), config.batch_size):
                    rows = expanded[start : start + config.batch_size]
                    negative = negatives[start : start + config.batch_size]
                    u, i = rows[:, 0], rows[:, 1]
                    # Gather copies so every gradient uses the same pre-update batch state.
                    p, q, r = user_factors[u], item_factors[i], item_factors[negative]
                    delta = np.einsum("ij,ij->i", p, q - r, optimize=False)
                    derivative = expit(-delta)[:, None]
                    sampled_losses.append((pairwise_loss(p, q, r, config.regularization), len(rows)))
                    rate, reg = config.learning_rate, config.regularization
                    # Sum per-triple gradients. One epoch visits each pair N times.
                    np.add.at(user_factors, u, rate * (derivative * (q - r) - reg * p))
                    np.add.at(item_factors, i, rate * (derivative * p - reg * q))
                    np.add.at(item_factors, negative, rate * (-derivative * p - reg * r))
                if not np.isfinite(user_factors).all() or not np.isfinite(item_factors).all():
                    raise ValueError(f"BPR vectors became nonfinite at epoch {epoch + 1}")
                fixed_loss = pairwise_loss(
                    user_factors[pair_array[:, 0]],
                    item_factors[pair_array[:, 1]],
                    item_factors[diagnostic_negatives],
                    config.regularization,
                )
                logs.append(
                    {
                        "epoch": epoch + 1,
                        "sampled_loss": sum(loss * n for loss, n in sampled_losses) / len(expanded),
                        "fixed_diagnostic_loss": fixed_loss,
                        "triples": len(expanded),
                    }
                )
        else:
            initial_loss = None

        model = cls(
            fallback,
            replace(fallback.metadata, model_version=model_version),
            config,
            users,
            items,
            signal_items,
            MappingProxyType(user_rows),
            FrozenFactors.from_array(user_factors),
            FrozenFactors.from_array(item_factors),
        )
        return model, {
            "configuration": asdict(config),
            "positive_behavior": "buy",
            "purchase_events": int(events["behavior_type"].eq("buy").sum()),
            "distinct_purchase_pairs": sum(len(p) for p in positives.values()),
            "trainable_positive_pairs": len(pairs),
            "purchase_users": len(positives),
            "trainable_users": len(users),
            "users_without_purchase": len(history.event_counts) - len(positives),
            "users_without_negative_candidates": len(positives) - len(users),
            "catalog_items": len(items),
            "positive_signal_items": len(set().union(*history.purchased.values())) if history.purchased else 0,
            "recommendable_signal_items": len(signal_items),
            "candidate_items_without_positive_signal": len(items) - len(signal_items),
            "parameter_count": user_factors.size + item_factors.size,
            "factor_bytes": user_factors.nbytes + item_factors.nbytes,
            "initial_fixed_diagnostic_loss": initial_loss,
            "epochs": logs,
            "fit_status": "trained" if pairs else "no_trainable_pairs",
            "training_snapshot_sha256": fallback.training_snapshot_sha256,
        }

    def recommend(self, user_id, k) -> Ranking[BPRPick]:
        """Final list order defines rank; fallback counts and latent scores have different units."""
        if not isinstance(user_id, str) or not 1 <= len(user_id) <= 128:
            raise ValueError("user_id must be a string of length 1–128")
        if type(k) is not int or k < 1:
            raise ValueError("k must be a positive integer")
        row = self._user_rows.get(user_id)
        if row is None:
            source = COLD_FALLBACK if user_id not in self.history.event_counts else NO_POSITIVE
            if self.history.blocked(user_id):
                source = NO_VECTOR
            popular = self.fallback.recommend(user_id, k)
            return Ranking(
                source, tuple(BPRPick(item.item_id, float(item.purchase_count), source) for item in popular.items)
            )
        signal_indices = np.array(
            [index for index, item in enumerate(self.items) if item in self.trained_items], dtype=np.int64
        )
        scores = np.einsum(
            "ij,j->i", self._item_factors.values[signal_indices], self._user_factors.values[row], optimize=False
        )
        if not np.isfinite(scores).all():
            popular = self.fallback.recommend(user_id, k)
            return Ranking(
                INVALID, tuple(BPRPick(item.item_id, float(item.purchase_count), INVALID) for item in popular.items)
            )
        blocked = self.history.blocked(user_id)
        allowed = np.array([self.items[index] not in blocked for index in signal_indices], dtype=bool)
        candidates, scores = signal_indices[allowed], scores[allowed]
        order = np.lexsort((candidates, -scores))[:k]
        picks = [BPRPick(self.items[candidates[index]], float(scores[index]), BPR) for index in order]
        selected = {pick.item_id for pick in picks}
        if len(picks) < k:
            for item in self.fallback.recommend(user_id, k).items:
                if item.item_id not in selected:
                    picks.append(BPRPick(item.item_id, float(item.purchase_count), FILL))
                    if len(picks) == k:
                        break
        source = BPR if len(order) == len(picks) else "bpr_with_popular_fill"
        return Ranking(source, tuple(picks))

    def rank_ids(self, user_id, k):
        return (item.item_id for item in self.recommend(user_id, k).items)
