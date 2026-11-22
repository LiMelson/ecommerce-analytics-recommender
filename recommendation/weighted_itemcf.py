"""Experimental sparse ItemCF with independent behavior and recency factors."""

from dataclasses import dataclass, replace
from math import isfinite
from types import MappingProxyType

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from .itemcf import ItemCFModel, _csr_bytes, _FittedCSR, binary_interactions
from .training_snapshot import training_snapshot_sha256

BEHAVIORS = ("pv", "cart", "buy")
EXPERIMENT_VERSIONS = {
    (True, False): "itemcf_weighted_exp_v1",
    (False, True): "itemcf_decay_exp_v1",
    (True, True): "itemcf_weighted_decay_exp_v1",
}


@dataclass(frozen=True)
class InteractionFactors:
    behavior_weights: tuple[float, float, float] | None
    half_life_days: float | None

    def __post_init__(self):
        weights = self.behavior_weights
        if weights is not None and (len(weights) != len(BEHAVIORS) or not all(isfinite(w) and w > 0 for w in weights)):
            raise ValueError("Each of pv, cart and buy needs a finite positive weight")
        days = self.half_life_days
        if days is not None and (not isfinite(days) or days <= 0):
            raise ValueError("Half-life must be finite and positive")


def interaction_values(events, retained_pairs, cutoff, factors):
    """Return features for already-retained pairs, independent of row order."""
    positive = events.loc[events["behavior_type"].isin(BEHAVIORS)]
    pair_keys = ["user_id", "item_id"]
    values = np.ones(len(retained_pairs), dtype=np.float64)
    if factors.behavior_weights is not None:
        counts = positive.groupby([*pair_keys, "behavior_type"], observed=True).size().unstack(fill_value=0)
        counts = counts.reindex(index=retained_pairs, columns=BEHAVIORS, fill_value=0)
        values *= np.sum(np.log1p(counts.to_numpy()) * factors.behavior_weights, axis=1)
    if factors.half_life_days is not None:
        last = positive.groupby(pair_keys, observed=True)["event_time"].max().reindex(retained_pairs)
        elapsed_days = (cutoff - last).dt.total_seconds().to_numpy() / 86400
        values *= np.exp2(-elapsed_days / factors.half_life_days)
    if not (np.isfinite(values) & (values > 0)).all():
        raise ValueError("Every retained pair must have a finite positive interaction value")
    return values


def weighted_cosine_neighbors(interactions, config):
    """Compute sparse cosine blocks and retain at most N neighbors per item."""
    item_users = interactions.T.tocsr()
    n_items = item_users.shape[0]
    norms = np.sqrt(np.asarray(interactions.power(2).sum(axis=0)).ravel())
    capacity = n_items * min(config.neighbors, max(n_items - 1, 0))
    indices = np.empty(capacity, dtype=np.int32)
    values = np.empty(capacity, dtype=np.float64)
    indptr = np.zeros(n_items + 1, dtype=np.int32)
    used = peak_block_nnz = peak_block_bytes = directed_pairs = 0
    for start in range(0, n_items, config.block_rows):
        end = min(start + config.block_rows, n_items)
        dot_products = item_users[start:end] @ interactions
        peak_block_nnz = max(peak_block_nnz, dot_products.nnz)
        peak_block_bytes = max(peak_block_bytes, _csr_bytes(dot_products))
        for local_row, item in enumerate(range(start, end)):
            left, right = dot_products.indptr[local_row : local_row + 2]
            cols = dot_products.indices[left:right]
            dots = dot_products.data[left:right]
            nonself = (cols != item) & (dots > 0)
            cols, dots = cols[nonself], dots[nonself]
            scores = dots / (norms[item] * norms[cols])
            valid = np.isfinite(scores) & (scores > 0)
            cols, scores = cols[valid], np.minimum(scores[valid], 1.0)
            directed_pairs += len(cols)
            order = np.lexsort((cols, -scores))[: config.neighbors]
            next_used = used + len(order)
            indices[used:next_used] = cols[order]
            values[used:next_used] = scores[order]
            indptr[item + 1] = used = next_used
    neighbors = csr_matrix((values[:used].copy(), indices[:used].copy(), indptr), shape=(n_items, n_items))
    return neighbors, {
        "directed_similarity_pairs_before_top_n": directed_pairs,
        "items_with_neighbors": int(np.count_nonzero(np.diff(indptr))),
        "retained_neighbors": neighbors.nnz,
        "neighbor_csr_bytes": _csr_bytes(neighbors),
        "block_rows_upper_bound": min(config.block_rows, n_items),
        "peak_block_nonzeros": peak_block_nnz,
        "peak_block_csr_bytes": peak_block_bytes,
    }


def fit_experimental_itemcf(events, fallback, config, factors):
    """Fit only the exact pre-cutoff snapshot shared with the frozen ranker."""
    history = fallback.history
    times = events["event_time"]
    if (
        not isinstance(times.dtype, pd.DatetimeTZDtype)
        or not times.lt(history.cutoff).all()
        or (history.fit_start is not None and not times.ge(history.fit_start).all())
    ):
        raise ValueError("Research ItemCF requires timezone-aware events within the fitted history window")
    if training_snapshot_sha256(events) != fallback.training_snapshot_sha256:
        raise ValueError("Research ItemCF requires the full shared training snapshot")
    version = EXPERIMENT_VERSIONS.get((factors.behavior_weights is not None, factors.half_life_days is not None))
    if version is None:
        raise ValueError("Experimental ItemCF needs behavior weights or decay; use ItemCFModel for the control")

    items = tuple(sorted(history.catalog))
    matrix, user_rows, profile = binary_interactions(events, items, config.max_history, positive_behaviors=BEHAVIORS)
    degrees = np.diff(matrix.indptr)
    indexed_users = np.asarray(tuple(user_rows), dtype=object).repeat(degrees)
    indexed_items = np.asarray(items, dtype=object)[matrix.indices]
    pairs = pd.MultiIndex.from_arrays((indexed_users, indexed_items), names=["user_id", "item_id"])
    values = interaction_values(events, pairs, history.cutoff, factors)
    weighted = csr_matrix((values, matrix.indices, matrix.indptr), shape=matrix.shape)
    neighbors, diagnostics = weighted_cosine_neighbors(weighted, config)
    model = ItemCFModel(
        fallback,
        items,
        replace(fallback.metadata, model_version=version),
        MappingProxyType(user_rows),
        _FittedCSR.from_matrix(weighted),
        _FittedCSR.from_matrix(neighbors),
    )
    return model, {
        **profile,
        **diagnostics,
        "interaction_shape": list(weighted.shape),
        "interaction_nonzeros": weighted.nnz,
        "interaction_csr_bytes": _csr_bytes(weighted),
        "neighbor_shape": list(neighbors.shape),
        "interaction_value_min": float(values.min()) if values.size else None,
        "interaction_value_max": float(values.max()) if values.size else None,
    }
