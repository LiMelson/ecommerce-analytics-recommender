"""Inspect the Parquet as delivered before normalizing it for training."""

from hashlib import file_digest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data_algo.data_algo.schema import AUX_BEHAVIORS, BEHAVIOR_MAP, CANONICAL_COLUMNS, CORE_BEHAVIORS
from data_algo.data_algo.website_export import export_website

from .benchmark import nearest_rank


def sha256_file(path):
    with open(path, "rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def read_sample(path, config):
    digest = sha256_file(path)
    if digest != config["sample_sha256"]:
        raise ValueError(f"Unexpected Parquet SHA-256: {digest}")
    table = pq.read_table(path)
    if set(table.column_names) != set(CANONICAL_COLUMNS) or len(table.column_names) != len(CANONICAL_COLUMNS):
        raise ValueError(f"Unexpected canonical Parquet schema: {table.schema}")
    schema = table.schema
    time_type = schema.field("event_time").type
    if not pa.types.is_timestamp(time_type) or time_type.tz not in (None, "UTC"):
        raise ValueError(f"Unexpected event_time type: {time_type}")
    if time_type.tz is None and config["naive_timestamp_timezone"] != "UTC":
        raise ValueError("Timezone-free timestamps require an explicit UTC source contract")
    if not pa.types.is_floating(schema.field("price").type):
        raise ValueError("price must be stored as a floating-point value")
    for name in set(CANONICAL_COLUMNS) - {"event_time", "price"}:
        typ = schema.field(name).type
        if not (pa.types.is_string(typ) or pa.types.is_large_string(typ)):
            raise ValueError(f"{name} must be stored as a string")
    storage = {
        "rows": table.num_rows,
        "types": {field.name: str(field.type) for field in schema},
        "null_rows": {name: table[name].null_count for name in table.column_names},
        "stored_timestamp_timezone": time_type.tz,
        "effective_timestamp_timezone": "UTC",
    }
    events = table.to_pandas()
    # The DuckDB exporter parsed source strings ending in UTC with TRY_STRPTIME.
    # Attach UTC to the stored clock values, without shifting them.
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True)
    if events.empty or events[["event_time", "user_id", "item_id", "behavior_type"]].isna().any().any():
        raise ValueError("Empty Parquet or nulls in required columns")
    if not events["behavior_type"].isin((*CORE_BEHAVIORS, *AUX_BEHAVIORS)).all():
        raise ValueError("Unknown behavior_type in the delivered sample")
    for name in ("user_id", "item_id", "category_id"):
        values = events[name].fillna("")
        valid = values.str.fullmatch(r"[^\x00-\x1f]{1,128}") & values.eq(values.str.strip())
        if name == "category_id":
            valid |= values.eq("")
        if not valid.all():
            raise ValueError(f"{name} contains {int((~valid).sum())} invalid identifiers")
    return events, storage, digest


def _sampling_check(events):
    users = events.loc[:, ["user_id"]].drop_duplicates()
    with duckdb.connect() as con:
        con.register("sample_users", users)
        result = con.execute(
            """
            WITH users AS (
                SELECT user_id, TRY_CAST(user_id AS BIGINT) AS numeric_id FROM sample_users
            )
            SELECT count(*) FILTER (WHERE numeric_id IS NOT NULL) AS numeric_users,
                   count(*) FILTER (WHERE numeric_id IS NULL) AS other_users,
                   count(*) FILTER (WHERE numeric_id % 1000 < 10) AS numeric_users_matching_rule,
                   count(*) FILTER (
                       WHERE numeric_id IS NULL AND hash(user_id || ':20261116') % 1000 < 10
                   ) AS other_users_matching_seed_20261116,
                   count(*) FILTER (
                       WHERE numeric_id IS NULL AND hash(user_id || ':20261117') % 1000 < 10
                   ) AS other_users_matching_seed_20261117
            FROM users
            """
        )
        return dict(zip((col[0] for col in result.description), result.fetchone(), strict=True))


def _website_check(events, config):
    # Match the handover loader's stable order without changing the audited row set.
    ordered = events.sort_values(["event_time", "user_id", "item_id"], kind="mergesort")
    with TemporaryDirectory() as directory:
        path = Path(directory) / "website_events.csv"
        report = export_website(ordered, path)
        digest = sha256_file(path)
        projected = pd.read_csv(path, dtype="string", keep_default_na=False)
    return {
        "columns": report["columns"],
        "source_rows": report["source_rows"],
        "exported_rows": report["exported_rows"],
        "excluded_by_type": report["excluded_by_type"],
        "timestamp_range_ms": report["timestamp_range"],
        "unknown_category_rows": report["unknown_category_rows"],
        "duplicate_projection_rows_after_first": int(projected.duplicated().sum()),
        "regenerated_sha256": digest,
        "handover_expected_sha256": config["handover_website_csv_sha256"],
        "matches_handover_checksum": digest == config["handover_website_csv_sha256"],
    }


def audit_sample(events, storage, digest, config):
    purchases = events.loc[events["behavior_type"].eq("buy")]
    buyer_counts = purchases.groupby("user_id").size()
    total_users = events["user_id"].nunique()
    frequencies = sorted(buyer_counts.tolist())
    histogram = {"0": total_users - len(buyer_counts)}
    for low, high in ((1, 1), (2, 3), (4, 7), (8, 15), (16, 31), (32, 63), (64, None)):
        key = f"{low}+" if high is None else str(low) if low == high else f"{low}–{high}"
        histogram[key] = sum(count >= low and (high is None or count <= high) for count in frequencies)
    return {
        "sample_sha256": digest,
        **storage,
        "users": total_users,
        "items": events["item_id"].nunique(),
        "min_event_time": events["event_time"].min().isoformat(),
        "max_event_time": events["event_time"].max().isoformat(),
        "behavior_counts": {
            kind: int(events["behavior_type"].eq(kind).sum()) for kind in (*CORE_BEHAVIORS, *AUX_BEHAVIORS)
        },
        "raw_behavior_counts": events["raw_event_type"].value_counts().to_dict(),
        "behavior_mapping_mismatches": int(
            events["raw_event_type"].map(BEHAVIOR_MAP).ne(events["behavior_type"]).sum()
        ),
        "exact_duplicate_rows_after_first": int(events.duplicated().sum()),
        "zero_price_rows": int(events["price"].eq(0).sum()),
        "negative_price_rows": int(events["price"].lt(0).sum()),
        "unknown_category_rows": int(events["category_id"].eq("unknown").sum()),
        "purchase_users": len(buyer_counts),
        "purchase_items": purchases["item_id"].nunique(),
        "distinct_user_item_purchase_pairs": len(purchases.drop_duplicates(["user_id", "item_id"])),
        "user_purchase_event_histogram": histogram,
        "buyer_purchase_event_quantiles": {
            f"p{q}": nearest_rank(frequencies, q / 100) if frequencies else None for q in (25, 50, 75, 90, 95, 100)
        },
        "sampling": _sampling_check(events),
        "website_export": _website_check(events, config),
    }
