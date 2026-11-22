"""Portable data-only Popular, ItemCF and BPR assets, including their fallback."""

import hashlib
import io
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from data_algo.data_algo.path_utils import atomic_output

from .benchmark import History
from .bpr import BPRModel
from .bpr_io import load_model as load_bpr
from .bpr_io import save_model as save_bpr
from .itemcf import ItemCFConfig, ItemCFModel, _FittedCSR
from .popular import Candidate, ModelMetadata, PopularModel

FORMAT = "recommendation-asset-v1"


class AssetError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def valid_id(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value == value.strip()
        and not any(ord(char) < 32 for char in value)
    )


def _json_bytes(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _popular_record(model):
    return {
        "metadata": asdict(model.metadata),
        "training_snapshot_sha256": model.training_snapshot_sha256,
        "fit_start": model.history.fit_start.isoformat() if model.history.fit_start is not None else None,
        "cutoff": model.history.cutoff.isoformat(),
        "event_counts": dict(model.history.event_counts),
        "purchased": {user: sorted(items) for user, items in model.history.purchased.items()},
        "ordered": [asdict(item) for item in model.ordered],
    }


def _restore_popular(record):
    metadata = ModelMetadata(**record["metadata"])
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", metadata.model_version):
        raise ValueError("Invalid model version")
    if not re.fullmatch(r"[a-f0-9]{64}", record["training_snapshot_sha256"]):
        raise ValueError("Invalid training snapshot fingerprint")
    cutoff = pd.Timestamp(record["cutoff"])
    fit_start = pd.Timestamp(record["fit_start"]) if record["fit_start"] else None
    if cutoff.tz is None or metadata.train_cutoff_exclusive != cutoff.isoformat():
        raise ValueError("Popular cutoff metadata mismatch")
    if fit_start is not None and (fit_start.tz is None or fit_start >= cutoff):
        raise ValueError("Invalid fitted time window")
    counts, purchased = record["event_counts"], record["purchased"]
    if any(not valid_id(user) or type(n) is not int or n < 1 for user, n in counts.items()):
        raise ValueError("Invalid user history mapping")
    ordered = tuple(Candidate(**item) for item in record["ordered"])
    catalog = frozenset(item.item_id for item in ordered)
    if len(catalog) != len(ordered):
        raise ValueError("Duplicate item in Popular ranking")
    if any(
        not valid_id(item.item_id)
        or not isinstance(item.category_id, str)
        or (item.category_id != "" and not valid_id(item.category_id))
        or type(item.purchase_count) is not int
        or item.purchase_count < 0
        for item in ordered
    ):
        raise ValueError("Invalid Popular item")
    if ordered != tuple(sorted(ordered, key=lambda item: (-item.purchase_count, item.item_id))):
        raise ValueError("Popular ranking order is inconsistent with scores")
    for user, items in purchased.items():
        if user not in counts or len(items) != len(set(items)) or not set(items).issubset(catalog):
            raise ValueError("Invalid purchase history")
    history = History(cutoff, catalog, counts, {user: frozenset(items) for user, items in purchased.items()}, fit_start)
    return PopularModel(metadata, history, ordered, record["training_snapshot_sha256"])


def _encode_csr(matrix, prefix):
    result = {}
    for field in ("data", "indices", "indptr"):
        buffer = io.BytesIO()
        np.save(buffer, getattr(matrix, field), allow_pickle=False)
        result[f"{prefix}_{field}.npy"] = buffer.getvalue()
    return result


def _decode_csr(blobs, prefix, shape, *, binary=False):
    arrays = [
        np.load(io.BytesIO(blobs[f"{prefix}_{name}.npy"]), allow_pickle=False) for name in ("data", "indices", "indptr")
    ]
    data, indices, indptr = arrays
    if data.dtype.kind not in "if" or indices.dtype.kind not in "iu" or indptr.dtype.kind not in "iu":
        raise ValueError("Invalid CSR buffer types")
    if any(array.ndim != 1 for array in arrays) or not np.isfinite(data).all():
        raise ValueError("Invalid CSR buffer shape or values")
    if (
        len(indices) != len(data)
        or len(indptr) != shape[0] + 1
        or indptr[0] != 0
        or indptr[-1] != len(data)
        or (indptr[1:] < indptr[:-1]).any()
        or (indices < 0).any()
        or (indices >= shape[1]).any()
        or (data <= 0).any()
        or (data > 1 + 1e-12).any()
    ):
        raise ValueError("CSR bounds or values are invalid")
    if binary and not (data == 1).all():
        raise ValueError("User interactions must be binary")
    for row in range(shape[0]):
        cols = indices[indptr[row] : indptr[row + 1]]
        if len(cols) != len(set(cols)) or (not binary and row in cols):
            raise ValueError("Duplicate CSR edge or self similarity")
    return _FittedCSR.from_matrix(csr_matrix((data, indices, indptr), shape=shape))


def save_asset(model, path, *, parameters, provenance, summary):
    """Write a self-contained asset and atomically replace only after full validation."""
    fallback = model if isinstance(model, PopularModel) else model.fallback
    kind = {PopularModel: "popular", ItemCFModel: "itemcf", BPRModel: "bpr"}.get(type(model))
    if kind is None:
        raise ValueError("Unsupported model type")
    if provenance.get("code_sha256") != model.metadata.code_sha256:
        raise ValueError("Asset provenance does not match model code")
    blobs = {"popular.json": _json_bytes(_popular_record(fallback))}
    extra = {}
    if kind == "itemcf":
        config = ItemCFConfig(**parameters)
        if len(model.items) and np.diff(model._neighbors.indptr).max() > config.neighbors:
            raise ValueError("ItemCF neighbor count exceeds declared parameters")
        blobs.update(_encode_csr(model._interactions, "interactions"))
        blobs.update(_encode_csr(model._neighbors, "neighbors"))
        extra = {
            "users": [user for user, _ in sorted(model._user_rows.items(), key=lambda entry: entry[1])],
            "items": model.items,
        }
    if kind == "bpr":
        if asdict(model.config) != parameters:
            raise ValueError("BPR configuration metadata mismatch")
        with TemporaryDirectory() as temp:
            nested = Path(temp) / "model.bpr"
            save_bpr(model, nested, provenance=provenance, training_summary=summary, evaluation={})
            blobs["model.bpr"] = nested.read_bytes()
    record = {
        "format": FORMAT,
        "kind": kind,
        "metadata": asdict(model.metadata),
        "training_snapshot_sha256": fallback.training_snapshot_sha256,
        "fit_start": fallback.history.fit_start.isoformat() if fallback.history.fit_start is not None else None,
        "parameters": parameters,
        "provenance": provenance,
        "summary": summary,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        **extra,
        "members_sha256": {name: _sha(data) for name, data in blobs.items()},
    }
    with atomic_output(path) as temporary:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(record))
            for name, data in blobs.items():
                archive.writestr(name, data)
        load_asset(
            temporary,
            expected_dataset_id=model.metadata.dataset_id,
            expected_model_version=model.metadata.model_version,
            expected_code_sha256=model.metadata.code_sha256,
        )


def load_asset(path, *, expected_dataset_id, expected_model_version, expected_code_sha256):
    """Identity values come from a trusted local registry, never from arbitrary request metadata."""
    if not Path(path).is_file():
        raise AssetError("model_data_missing", "Model asset file is missing")
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len({info.filename for info in infos}) != len(infos) or any(
                info.file_size > 512 * 1024**2 for info in infos
            ):
                raise ValueError("Duplicate or oversized model asset members")
            record = json.loads(archive.read("manifest.json"))
            if record["format"] != FORMAT or record["kind"] not in ("popular", "itemcf", "bpr"):
                raise ValueError("Unsupported asset format or kind")
            expected = {"manifest.json", *record["members_sha256"]}
            if set(archive.namelist()) != expected:
                raise ValueError("Model asset members do not match manifest")
            blobs = {name: archive.read(name) for name in record["members_sha256"]}
            if any(_sha(blobs[name]) != digest for name, digest in record["members_sha256"].items()):
                raise ValueError("Model asset member checksum mismatch")
        metadata = ModelMetadata(**record["metadata"])
        if metadata.dataset_id != expected_dataset_id:
            raise AssetError("dataset_mismatch", "Model asset dataset does not match registry")
        if metadata.model_version != expected_model_version or metadata.code_sha256 != expected_code_sha256:
            raise AssetError(
                "model_identity_mismatch", "Model asset version or source identity does not match registry"
            )
        fallback = _restore_popular(json.loads(blobs["popular.json"]))
        if (
            any(
                getattr(metadata, name) != getattr(fallback.metadata, name)
                for name in ("dataset_id", "sample_sha256", "protocol_version", "train_cutoff_exclusive", "code_sha256")
            )
            or record["training_snapshot_sha256"] != fallback.training_snapshot_sha256
        ):
            raise ValueError("Fallback identity does not match primary model")
        if (
            record["fit_start"]
            != (fallback.history.fit_start.isoformat() if fallback.history.fit_start is not None else None)
            or record["provenance"]["code_sha256"] != metadata.code_sha256
        ):
            raise ValueError("Asset provenance mismatch")
        if record["kind"] == "popular":
            if metadata != fallback.metadata or set(blobs) != {"popular.json"}:
                raise ValueError("Popular identity or members mismatch")
            return fallback, record
        if record["kind"] == "itemcf":
            config = ItemCFConfig(**record["parameters"])
            users, items = tuple(record["users"]), tuple(record["items"])
            if (
                list(users) != sorted(set(users))
                or not set(users).issubset(fallback.history.event_counts)
                or items != tuple(sorted(fallback.history.catalog))
            ):
                raise ValueError("ItemCF ID mapping mismatch")
            interactions = _decode_csr(blobs, "interactions", (len(users), len(items)), binary=True)
            neighbors = _decode_csr(blobs, "neighbors", (len(items), len(items)))
            if len(items) and (np.diff(neighbors.indptr) > config.neighbors).any():
                raise ValueError("Neighbor configuration mismatch")
            if config.max_history is not None and (np.diff(interactions.indptr) > config.max_history).any():
                raise ValueError("User history limit mismatch")
            model = ItemCFModel(
                fallback,
                items,
                metadata,
                MappingProxyType(dict(zip(users, range(len(users)), strict=True))),
                interactions,
                neighbors,
            )
        else:
            if set(blobs) != {"popular.json", "model.bpr"}:
                raise ValueError("BPR asset member mismatch")
            with TemporaryDirectory() as temp:
                nested = Path(temp) / "model.bpr"
                nested.write_bytes(blobs["model.bpr"])
                model = load_bpr(
                    nested, fallback, model_version=expected_model_version, code_sha256=expected_code_sha256
                )
            if asdict(model.config) != record["parameters"] or model.metadata != metadata:
                raise ValueError("BPR outer and inner metadata mismatch")
        return model, record
    except AssetError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        RuntimeError,
        BadZipFile,
        EOFError,
    ) as error:
        raise AssetError("model_corrupt", f"Cannot load model asset: {error}") from error
