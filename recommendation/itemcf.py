"""Binary implicit ItemCF using bounded sparse co-occurrence blocks."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from heapq import nsmallest
from types import MappingProxyType

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from data_algo.data_algo.schema import CORE_BEHAVIORS

from .benchmark import nearest_rank
from .popular import COLD_FALLBACK, ModelMetadata, PopularModel, Ranking
from .training_snapshot import training_snapshot_sha256

ITEMCF = "itemcf_cosine"
FILL = "popular_fill"
NO_NEIGHBORS = "popular_no_neighbors"
MIXED = "itemcf_with_popular_fill"


@dataclass(frozen=True)
class ItemCFConfig:
    neighbors: int
    block_rows: int
    max_history: int | None = None

    def __post_init__(self):
        for name, value in (("neighbors", self.neighbors), ("block_rows", self.block_rows)):
            if type(value) is not int or not 1 <= value <= 256:
                raise ValueError(f"{name} must be an integer between 1 and 256")
        if self.max_history is not None and (type(self.max_history) is not int or self.max_history < 1):
            raise ValueError("max_history must be a positive integer or None")


def _csr_bytes(matrix):
    return matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes


def _degrees_profile(degrees):
    counts = sorted(degrees.tolist())
    return {
        "positive_users": len(counts),
        "unique_pairs": int(degrees.sum()),
        "per_user_degree": {
            f"p{percentile}": nearest_rank(counts, percentile / 100) if counts else None
            for percentile in (50, 90, 95, 99, 100)
        },
        "unordered_cooccurrence_contributions": int((degrees * (degrees - 1) // 2).sum()),
    }


def binary_interactions(events, items, max_history, *, positive_behaviors=CORE_BEHAVIORS):
    """Build a binary matrix; a future weighting policy can replace this builder."""
    positive = events.loc[events["behavior_type"].isin(positive_behaviors), ["user_id", "item_id", "event_time"]]
    if max_history is not None:
        positive = positive.sort_values(["user_id", "event_time", "item_id"], ascending=[True, False, True])
    pairs = positive.drop_duplicates(["user_id", "item_id"])
    degrees = pairs.groupby("user_id", sort=False).size()
    used = pairs
    if max_history is not None:
        position = pairs.groupby("user_id", sort=False).cumcount()
        used = pairs.loc[position.lt(max_history)]
    users = pd.Index(sorted(degrees.index))
    user_index = users.get_indexer(used["user_id"])
    item_index = pd.Index(items).get_indexer(used["item_id"])
    if (item_index == -1).any():
        raise ValueError("Positive feedback contains an item outside the fitted catalog")
    matrix = csr_matrix(
        (np.ones(len(used), dtype=np.int32), (user_index, item_index)),
        shape=(len(users), len(items)),
    )
    degree_values = degrees.to_numpy(dtype=np.int64)
    kept_degrees = np.diff(matrix.indptr).astype(np.int64)
    return (
        matrix,
        {user_id: row for row, user_id in enumerate(users)},
        {
            "positive_events": len(positive),
            "before_history_limit": _degrees_profile(degree_values),
            "after_history_limit": _degrees_profile(kept_degrees),
            "users_truncated": int((degree_values > max_history).sum()) if max_history is not None else 0,
        },
    )


def cosine_neighbors(interactions, config):
    """Multiply small item×user row blocks and retain only each item's TopN."""
    item_users = interactions.T.tocsr()
    n_items = item_users.shape[0]
    norms = np.sqrt(np.diff(item_users.indptr).astype(np.float64))
    capacity = n_items * min(config.neighbors, max(n_items - 1, 0))
    indices = np.empty(capacity, dtype=np.int32)
    values = np.empty(capacity, dtype=np.float64)
    indptr = np.zeros(n_items + 1, dtype=np.int32)
    used = peak_block_nnz = peak_block_bytes = directed_pairs = 0
    for start in range(0, n_items, config.block_rows):
        end = min(start + config.block_rows, n_items)
        counts = item_users[start:end] @ interactions
        peak_block_nnz = max(peak_block_nnz, counts.nnz)
        peak_block_bytes = max(peak_block_bytes, _csr_bytes(counts))
        for local_row, item in enumerate(range(start, end)):
            left, right = counts.indptr[local_row : local_row + 2]
            cols = counts.indices[left:right]
            support = counts.data[left:right]
            valid = (cols != item) & (support > 0)
            cols, support = cols[valid], support[valid]
            scores = support / (norms[item] * norms[cols])
            finite = np.isfinite(scores) & (scores > 0)
            cols, scores = cols[finite], scores[finite]
            directed_pairs += len(cols)
            order = np.lexsort((cols, -scores))[: config.neighbors]
            next_used = used + len(order)
            indices[used:next_used] = cols[order]
            values[used:next_used] = scores[order]
            indptr[item + 1] = next_used
            used = next_used
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


@dataclass(frozen=True)
class ItemCFPick:
    """A ranked item with a source-specific diagnostic score."""

    item_id: str
    score: float
    source: str


@dataclass(frozen=True)
class _FittedCSR:
    """Own immutable copies of the CSR buffers used by the ranker."""

    shape: tuple[int, int]
    _data: tuple[bytes, np.dtype]
    _indices: tuple[bytes, np.dtype]
    _indptr: tuple[bytes, np.dtype]

    @classmethod
    def from_matrix(cls, matrix):
        buffers = ((array.tobytes(), array.dtype) for array in (matrix.data, matrix.indices, matrix.indptr))
        return cls(matrix.shape, *buffers)

    @property
    def data(self):
        return np.frombuffer(*self._data)

    @property
    def indices(self):
        return np.frombuffer(*self._indices)

    @property
    def indptr(self):
        return np.frombuffer(*self._indptr)


@dataclass(frozen=True)
class ItemCFModel:
    fallback: PopularModel
    items: tuple[str, ...]
    metadata: ModelMetadata
    _user_rows: Mapping[str, int]
    _interactions: _FittedCSR
    _neighbors: _FittedCSR

    @property
    def history(self):
        return self.fallback.history

    @classmethod
    def fit(cls, events, fallback, config, *, model_version):
        history = fallback.history
        times = events["event_time"]
        if not times.lt(history.cutoff).all() or (
            history.fit_start is not None and not times.ge(history.fit_start).all()
        ):
            raise ValueError("ItemCF training events are outside the fitted history window")
        if len(events) != sum(history.event_counts.values()):
            raise ValueError("ItemCF must use the full shared history for candidate and purchase policy")
        if training_snapshot_sha256(events) != fallback.training_snapshot_sha256:
            raise ValueError("ItemCF training snapshot content does not match the fitted Popular model")
        items = tuple(sorted(history.catalog))
        matrix, user_rows, profile = binary_interactions(events, items, config.max_history)
        neighbors, diagnostics = cosine_neighbors(matrix, config)
        metadata = replace(fallback.metadata, model_version=model_version)
        model = cls(
            fallback,
            items,
            metadata,
            MappingProxyType(user_rows),
            _FittedCSR.from_matrix(matrix),
            _FittedCSR.from_matrix(neighbors),
        )
        return model, {
            **profile,
            **diagnostics,
            "interaction_shape": list(matrix.shape),
            "interaction_nonzeros": matrix.nnz,
            "interaction_csr_bytes": _csr_bytes(matrix),
            "neighbor_shape": list(neighbors.shape),
        }

    def recommend(self, user_id: str, k: int) -> Ranking[ItemCFPick]:
        """Return final rank order; raw scores from different sources are incomparable."""
        # PopularModel validates the arguments and applies the same purchase filter.
        row = self._user_rows.get(user_id) if isinstance(user_id, str) else None
        if row is None:
            popular = self.fallback.recommend(user_id, k)
            source = COLD_FALLBACK if popular.source == COLD_FALLBACK else NO_NEIGHBORS
            return Ranking(
                source, tuple(ItemCFPick(item.item_id, float(item.purchase_count), source) for item in popular.items)
            )

        if type(k) is not int or k < 1:
            raise ValueError("k must be a positive integer")
        blocked = self.history.blocked(user_id)
        scores = defaultdict(float)
        left, right = self._interactions.indptr[row : row + 2]
        neighbor_offsets, neighbor_items, similarities = (
            self._neighbors.indptr,
            self._neighbors.indices,
            self._neighbors.data,
        )
        for seed in self._interactions.indices[left:right]:
            first, last = neighbor_offsets[seed : seed + 2]
            for index, similarity in zip(neighbor_items[first:last], similarities[first:last], strict=True):
                if self.items[index] not in blocked:
                    scores[index] += float(similarity)
        best = nsmallest(k, scores.items(), key=lambda entry: (-entry[1], entry[0]))
        picks = [ItemCFPick(self.items[index], score, ITEMCF) for index, score in best]
        if len(picks) == k:
            return Ranking(ITEMCF, tuple(picks))

        fallback_source = FILL if picks else NO_NEIGHBORS
        selected = {pick.item_id for pick in picks}
        for item in self.fallback.recommend(user_id, k).items:
            if item.item_id not in selected:
                picks.append(ItemCFPick(item.item_id, float(item.purchase_count), fallback_source))
                if len(picks) == k:
                    break
        source = ITEMCF if best else NO_NEIGHBORS
        if best and len(picks) > len(best):
            source = MIXED
        return Ranking(source, tuple(picks))

    def rank_ids(self, user_id, k):
        return (item.item_id for item in self.recommend(user_id, k).items)
