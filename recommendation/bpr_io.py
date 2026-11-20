"""Safe, atomic single-file BPR artifact with numeric arrays and JSON metadata."""

import hashlib
import io
import json
from dataclasses import asdict
from types import MappingProxyType
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from data_algo.data_algo.path_utils import atomic_output

from .bpr import BPRConfig, BPRModel, FrozenFactors
from .popular import ModelMetadata

FORMAT = "bpr-mf-numpy-v1"


def _array_bytes(values):
    buffer = io.BytesIO()
    np.save(buffer, values, allow_pickle=False)
    return buffer.getvalue()


def save_model(model, path, *, provenance, training_summary, evaluation):
    if not provenance.get("code_sha256") == model.metadata.code_sha256:
        raise ValueError("Provenance code fingerprint does not match the model")
    arrays = {
        "user_factors.npy": _array_bytes(model._user_factors.values),
        "item_factors.npy": _array_bytes(model._item_factors.values),
    }
    record = {
        "format": FORMAT,
        "metadata": asdict(model.metadata),
        "training_snapshot_sha256": model.fallback.training_snapshot_sha256,
        "configuration": asdict(model.config),
        "users": model.users,
        "items": model.items,
        "trained_items": sorted(model.trained_items),
        "array_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in arrays.items()},
        "provenance": provenance,
        "training_summary": training_summary,
        "evaluation": evaluation,
    }
    with atomic_output(path) as temporary, ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False))
        for name, data in arrays.items():
            archive.writestr(name, data)


def load_model(path, fallback, *, model_version, code_sha256):
    """Load data-only arrays and require the caller's trusted fallback and expected identity."""
    with ZipFile(path) as archive:
        expected = {"metadata.json", "user_factors.npy", "item_factors.npy"}
        if len(archive.infolist()) != len(expected) or set(archive.namelist()) != expected:
            raise ValueError("Unexpected BPR artifact members")
        if any(info.file_size > 512 * 1024**2 for info in archive.infolist()):
            raise ValueError("BPR artifact member exceeds the 512 MiB limit")
        record = json.loads(archive.read("metadata.json"))
        if record["format"] != FORMAT:
            raise ValueError("Unsupported BPR artifact format")
        config = BPRConfig(**record["configuration"])
        metadata = ModelMetadata(**record["metadata"])
        for field in ("dataset_id", "sample_sha256", "protocol_version", "train_cutoff_exclusive"):
            if getattr(metadata, field) != getattr(fallback.metadata, field):
                raise ValueError(f"BPR artifact {field} does not match the requested snapshot")
        if (
            metadata.model_version != model_version
            or metadata.code_sha256 != code_sha256
            or record["training_snapshot_sha256"] != fallback.training_snapshot_sha256
            or record["provenance"]["code_sha256"] != code_sha256
        ):
            raise ValueError("BPR artifact model, code or training snapshot identity does not match")

        users, items = tuple(record["users"]), tuple(record["items"])
        trained = frozenset(record["trained_items"])
        if (
            any(not isinstance(value, str) for value in (*users, *items))
            or list(users) != sorted(set(users))
            or items != tuple(sorted(fallback.history.catalog))
            or not set(users).issubset(fallback.history.purchased)
            or not trained.issubset(fallback.history.catalog)
        ):
            raise ValueError("BPR artifact ID mapping does not match training history")
        expected_users = tuple(
            user for user, purchases in sorted(fallback.history.purchased.items()) if 0 < len(purchases) < len(items)
        )
        expected_signal = frozenset(item for user in expected_users for item in fallback.history.blocked(user))
        if users != expected_users or trained != expected_signal:
            raise ValueError("BPR artifact signal and user mapping do not match training positives")
        arrays = []
        for name, rows in (("user_factors.npy", len(users)), ("item_factors.npy", len(items))):
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != record["array_sha256"][name]:
                raise ValueError("BPR artifact array checksum mismatch")
            values = np.load(io.BytesIO(data), allow_pickle=False)
            if values.dtype != np.float64 or values.shape != (rows, config.dimensions) or not np.isfinite(values).all():
                raise ValueError("BPR artifact contains invalid factors")
            arrays.append(FrozenFactors.from_array(values))
    return BPRModel(
        fallback,
        metadata,
        config,
        users,
        items,
        trained,
        MappingProxyType({user: row for row, user in enumerate(users)}),
        *arrays,
    )
