"""Deterministic purchase-count popularity and the existing prediction contract."""

from dataclasses import dataclass
from itertools import islice
from re import fullmatch
from typing import Generic, TypeVar

from .benchmark import History
from .training_snapshot import training_snapshot_sha256

POPULAR = "popular_purchase_count"
COLD_FALLBACK = "cold_start_popular_purchase_count"
RankedItem = TypeVar("RankedItem")


@dataclass(frozen=True)
class ModelMetadata:
    dataset_id: str
    model_version: str
    sample_sha256: str
    protocol_version: str
    train_cutoff_exclusive: str
    code_sha256: str

    def __post_init__(self):
        if not fullmatch(r"[a-f0-9]{64}", self.sample_sha256):
            raise ValueError("sample_sha256 must be a lowercase SHA-256 checksum")
        if self.dataset_id != local_dataset_id(self.sample_sha256) and not fullmatch(r"[a-f0-9]{32}", self.dataset_id):
            raise ValueError("Dataset ID must match the local sample checksum or have the backend dataset ID format")


def local_dataset_id(sha256):
    return f"local-parquet-sha256-{sha256}"


@dataclass(frozen=True)
class Candidate:
    item_id: str
    category_id: str
    purchase_count: int


@dataclass(frozen=True)
class Ranking(Generic[RankedItem]):
    source: str
    items: tuple[RankedItem, ...]


@dataclass(frozen=True)
class PopularModel:
    metadata: ModelMetadata
    history: History
    ordered: tuple[Candidate, ...]
    training_snapshot_sha256: str

    @classmethod
    def fit(
        cls, events, cutoff, *, dataset_id, model_version, sample_sha256, protocol_version, code_sha256, fit_start=None
    ):
        history = History.fit(events, cutoff, fit_start=fit_start)
        snapshot_sha256 = training_snapshot_sha256(events)
        # At equal timestamps, choose the smallest category string for that item.
        known = events.loc[events["category_id"].notna() & ~events["category_id"].isin(["", "unknown"])]
        latest = known.sort_values(["event_time", "category_id"], ascending=[False, True])
        categories = latest.drop_duplicates("item_id").set_index("item_id")["category_id"].to_dict()
        counts = events.loc[events["behavior_type"].eq("buy"), "item_id"].value_counts().to_dict()
        ordered = sorted(history.catalog, key=lambda item: (-counts.get(item, 0), item))
        return cls(
            ModelMetadata(dataset_id, model_version, sample_sha256, protocol_version, cutoff.isoformat(), code_sha256),
            history,
            tuple(Candidate(item, categories.get(item, ""), counts.get(item, 0)) for item in ordered),
            snapshot_sha256,
        )

    def recommend(self, user_id: str, k: int) -> Ranking[Candidate]:
        if not isinstance(user_id, str) or not 1 <= len(user_id) <= 128:
            raise ValueError("user_id must be a string of length 1–128")
        if type(k) is not int or k < 1:
            raise ValueError("k must be a positive integer")
        blocked = self.history.blocked(user_id)
        source = POPULAR if user_id in self.history.event_counts else COLD_FALLBACK
        if not blocked:
            return Ranking(source, self.ordered[:k])
        items = islice((item for item in self.ordered if item.item_id not in blocked), k)
        return Ranking(source, tuple(items))

    def rank_ids(self, user_id, k):
        return (item.item_id for item in self.recommend(user_id, k).items)

    def predict(self, *, user_id: str, dataset_id: str, model_version: str, limit: int = 10):
        """Map the prebuilt model to the backend provider's strict JSON schema."""
        if (dataset_id, model_version) != (self.metadata.dataset_id, self.metadata.model_version):
            raise ValueError("Request dataset_id or model_version does not match model metadata")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be an integer between 1 and 10")
        ranking = self.recommend(user_id, limit)
        prefix = "训练期无用户历史，使用热门兜底" if ranking.source == COLD_FALLBACK else "依据训练期商品热度推荐"
        return {
            "user_id": user_id,
            "dataset_id": dataset_id,
            "model_version": model_version,
            "items": [
                {
                    "item_id": item.item_id,
                    "category_id": item.category_id,
                    "rank": rank,
                    "score": float(item.purchase_count),
                    "reason": f"{prefix}（商品购买事件 {item.purchase_count} 次）",
                }
                for rank, item in enumerate(ranking.items, 1)
            ],
        }
