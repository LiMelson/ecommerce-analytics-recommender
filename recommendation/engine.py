"""Pure-Python inference API backed by explicit local model registrations."""

import json
import math
import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType

from .assets import AssetError, load_asset, valid_id
from .bpr import BPRModel
from .itemcf import ItemCFModel
from .popular import COLD_FALLBACK, PopularModel
from .sample import sha256_file

REASONS = {
    "itemcf_cosine": "依据训练期二值交互的商品余弦相似度推荐",
    "bpr_mf": "依据购买正反馈训练的BPR用户商品向量内积推荐",
    "popular_fill": "ItemCF相似候选不足，按训练期购买热度补位",
    "popular_bpr_fill": "BPR有效候选不足，按训练期购买热度补位",
    "popular_no_neighbors": "没有有效相似邻居，使用训练期购买热门兜底",
    "popular_no_bpr_positive": "用户有行为历史但无购买正反馈，使用训练期热门兜底",
    "popular_no_trainable_vector": "用户没有可用训练因子，使用训练期热门兜底",
    "popular_invalid_bpr_score": "BPR偏好分数无法有效计算，使用训练期热门兜底",
    COLD_FALLBACK: "训练截止前无用户历史，使用购买热门兜底",
    "popular_purchase_count": "依据训练期商品购买事件次数推荐",
}


class RecommendationEngine:
    def __init__(self):
        self._registrations = {}
        self._models = {}
        self._categories = {}
        self._asset_metadata = {}

    @classmethod
    def from_models(cls, models):
        engine = cls()
        for model in models:
            engine._install(model)
        return engine

    @classmethod
    def from_registry(cls, path, *, preload=True):
        path = Path(path).resolve()
        if not path.is_file():
            raise AssetError("model_data_missing", "Model registry is missing")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["format"] != "recommendation-registry-v1" or not record["models"]:
                raise ValueError("Unsupported or empty model registry")
            engine = cls()
            for version, registration in record["models"].items():
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", version):
                    raise ValueError("Invalid registered model version")
                if not {"file", "kind", "sha256", "dataset_id", "code_sha256"}.issubset(registration):
                    raise ValueError("Incomplete model registration")
                if registration["kind"] not in ("popular", "itemcf", "bpr") or not re.fullmatch(
                    r"[a-f0-9]{64}", registration["code_sha256"]
                ):
                    raise ValueError("Invalid registered model kind or code fingerprint")
                asset = (path.parent / registration["file"]).resolve()
                if not asset.is_relative_to(path.parent) or Path(registration["file"]).is_absolute():
                    raise ValueError("Registry asset must stay within its local directory")
                if not re.fullmatch(r"[a-f0-9]{64}", registration["sha256"]):
                    raise ValueError("Invalid asset hash")
                engine._registrations[version] = {**registration, "path": asset}
            if preload:
                for version in engine._registrations:
                    engine.load(version)
            return engine
        except AssetError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            raise AssetError("model_corrupt", f"Cannot read model registry: {error}") from error

    def _install(self, model):
        if type(model) not in (PopularModel, ItemCFModel, BPRModel):
            raise AssetError("model_identity_mismatch", "Unsupported model type")
        version = model.metadata.model_version
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", version) or version in self._models:
            raise AssetError("model_identity_mismatch", "Invalid or duplicate model version")
        fallback = model if isinstance(model, PopularModel) else model.fallback
        if any(
            getattr(model.metadata, name) != getattr(fallback.metadata, name)
            for name in ("dataset_id", "sample_sha256", "protocol_version", "train_cutoff_exclusive", "code_sha256")
        ):
            raise AssetError("model_identity_mismatch", "Model and fallback identity differ")
        self._models[version] = model
        self._categories[version] = MappingProxyType({item.item_id: item.category_id for item in fallback.ordered})

    def load(self, model_version):
        if not isinstance(model_version, str) or model_version not in self._registrations:
            raise AssetError("unknown_model", "Model version is not registered")
        registration = self._registrations[model_version]
        path = registration["path"]
        if not path.is_file():
            raise AssetError("model_data_missing", "Registered model asset is missing")
        if sha256_file(path) != registration["sha256"]:
            raise AssetError("model_corrupt", "Registered model asset checksum does not match")
        model, metadata = load_asset(
            path,
            expected_dataset_id=registration["dataset_id"],
            expected_model_version=model_version,
            expected_code_sha256=registration["code_sha256"],
        )
        if metadata["kind"] != registration["kind"]:
            raise AssetError("model_identity_mismatch", "Registered model kind does not match asset")
        # A failed load never replaces an already installed model.
        if model_version in self._models:
            return
        self._install(model)
        self._asset_metadata[model_version] = metadata

    def versions(self):
        return sorted(set(self._registrations) | set(self._models))

    def describe(self, model_version):
        model = self._get_model(model_version)
        fallback = model if isinstance(model, PopularModel) else model.fallback
        result = {
            "model": asdict(model.metadata),
            "training_snapshot_sha256": fallback.training_snapshot_sha256,
            "loaded": True,
            "candidate_items": len(model.history.catalog),
        }
        if model_version in self._asset_metadata:
            manifest = self._asset_metadata[model_version]
            result.update(
                {
                    key: deepcopy(manifest[key])
                    for key in ("kind", "parameters", "fit_start", "built_at_utc", "provenance")
                }
            )
        return result

    def _get_model(self, version):
        if not isinstance(version, str) or version not in self.versions():
            raise AssetError("unknown_model", "Model version is not registered")
        if version not in self._models:
            raise AssetError("model_not_loaded", "Registered model has not been loaded")
        return self._models[version]

    def predict_with_trace(self, *, user_id, dataset_id, model_version, limit=10):
        if not valid_id(user_id):
            raise AssetError(
                "invalid_user_id", "User ID must be a 1–128 character string without surrounding whitespace or controls"
            )
        if type(limit) is not int or not 1 <= limit <= 10:
            raise AssetError("invalid_limit", "limit must be an integer between 1 and 10")
        model = self._get_model(model_version)
        if dataset_id != model.metadata.dataset_id:
            raise AssetError("dataset_mismatch", "Requested dataset does not match the loaded model")
        ranking = model.recommend(user_id, limit)
        items, sources, selected = [], [], set()
        for rank, pick in enumerate(ranking.items, 1):
            source = getattr(pick, "source", ranking.source)
            score = float(pick.purchase_count) if isinstance(model, PopularModel) else float(pick.score)
            if (
                pick.item_id in selected
                or pick.item_id not in model.history.catalog
                or pick.item_id in model.history.blocked(user_id)
                or not math.isfinite(score)
                or source not in REASONS
                or rank > limit
            ):
                raise AssetError("model_invalid_output", "Model returned an invalid recommendation")
            selected.add(pick.item_id)
            sources.append(source)
            items.append(
                {
                    "item_id": pick.item_id,
                    "category_id": self._categories[model_version][pick.item_id],
                    "rank": rank,
                    "score": score,
                    "reason": REASONS[source],
                }
            )
        response = {"user_id": user_id, "dataset_id": dataset_id, "model_version": model_version, "items": items}
        fallback_reason = None
        if sources and any(source not in ("itemcf_cosine", "bpr_mf", "popular_purchase_count") for source in sources):
            fallback_reason = ranking.source
        if not model.history.catalog:
            fallback_reason = "empty_candidate_catalog"
        return response, {"source": ranking.source, "item_sources": sources, "fallback_reason": fallback_reason}

    def predict(self, *, user_id, dataset_id, model_version, limit=10):
        response, _ = self.predict_with_trace(
            user_id=user_id, dataset_id=dataset_id, model_version=model_version, limit=limit
        )
        return response

    def predict_batch(self, user_ids, *, dataset_id, model_version, limit=10):
        model = self._get_model(model_version)
        if dataset_id != model.metadata.dataset_id:
            raise AssetError("dataset_mismatch", "Requested dataset does not match the loaded model")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise AssetError("invalid_limit", "limit must be an integer between 1 and 10")
        if isinstance(user_ids, (str, bytes)) or not isinstance(user_ids, Iterable):
            raise AssetError("invalid_batch", "Batch input must be an iterable of user ID strings")
        return [
            self.predict(user_id=user_id, dataset_id=dataset_id, model_version=model_version, limit=limit)
            for user_id in user_ids
        ]
