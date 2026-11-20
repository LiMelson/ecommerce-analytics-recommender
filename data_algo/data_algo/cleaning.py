"""数据清洗、质量扫描与按用户抽样。

设计原则
--------
1. 原始文件只读，不修改、不删除；
2. 大文件按块读取，不一次性塞进内存；
3. 抽样以“用户”为单位，保留被抽中用户的完整历史；
4. 所有无效行、重复行、缺失值都记录下来，生成可复查的质量报告；
5. 金额只做“行为口径成交额”，不冒充订单 GMV。

原始字段（Tianchi 220316）：
    event_time, event_type, product_id, category_id, category_code,
    brand, price, user_id, user_session
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .io_utils import write_json, write_text
from .path_utils import ensure_distinct_paths, temp_output_path
from .schema import (
    BEHAVIOR_MAP,
    CANONICAL_COLUMNS,
    CN_TZ,
    RAW_OPTIONAL_COLUMNS,
    RAW_REQUIRED_COLUMNS,
    UTC,
)


# 抽样输出的 Parquet 结构（固定下来，方便后端直接读）
SAMPLE_SCHEMA = pa.schema(
    [
        ("user_id", pa.string()),
        ("item_id", pa.string()),
        ("category_id", pa.string()),
        ("category_code", pa.string()),
        ("brand", pa.string()),
        ("behavior_type", pa.string()),
        ("raw_event_type", pa.string()),
        ("event_time", pa.timestamp("ns", tz=UTC)),
        ("price", pa.float64()),
        ("session_id", pa.string()),
    ]
)

DUPLICATE_SUBSET = [
    "user_id",
    "item_id",
    "category_id",
    "category_code",
    "brand",
    "behavior_type",
    "raw_event_type",
    "event_time",
    "price",
    "session_id",
]


class BoundedSet:
    """有上限的去重集合，避免全量数据把内存撑爆。"""

    def __init__(self, cap: int = 2_000_000) -> None:
        self.cap = cap
        self._values: set[str] = set()
        self.truncated = False

    def add_many(self, values: pd.Series) -> None:
        if self.truncated:
            return
        for value in values.dropna().astype(str).unique():
            if value == "":
                continue
            self._values.add(value)
            if len(self._values) >= self.cap:
                self.truncated = True
                return

    def result(self) -> dict:
        return {
            "count": len(self._values),
            "truncated": self.truncated,
            "note": (
                f"已达到统计上限 {self.cap:,}，实际数量更多"
                if self.truncated
                else "精确计数"
            ),
        }


def validate_header(path: str | Path) -> list[str]:
    """检查 CSV 表头，返回实际列名。"""
    p = Path(path)
    header = pd.read_csv(p, nrows=0, encoding="utf-8-sig").columns.tolist()
    missing = [c for c in RAW_REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ValueError(
            f"{p.name} 缺少必需列: {missing}；实际列为: {header}"
        )
    return header


def parse_event_time(values: pd.Series) -> pd.Series:
    """解析 ``2019-10-01 00:00:00 UTC`` 这类时间，统一为 UTC。"""
    text = values.astype("string").str.strip()
    cleaned = text.str.replace(r"\s+UTC$", "", regex=True)
    parsed = pd.to_datetime(
        cleaned,
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce",
        utc=True,
    )
    # 兼容其他常见格式（ISO8601、带时区偏移等）
    fallback = parsed.isna() & cleaned.notna() & cleaned.ne("")
    if fallback.any():
        parsed2 = pd.to_datetime(cleaned[fallback], errors="coerce", utc=True)
        parsed.loc[fallback] = parsed2
    return parsed


def _empty_if_missing(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    return out


def normalize_chunk(
    raw: pd.DataFrame,
    *,
    row_offset: int = 0,
    file_name: str = "",
    max_examples: int = 0,
) -> tuple[pd.DataFrame, dict, list[dict]]:
    """清洗一个数据块，返回规范数据、统计信息和无效行样例。

    ``row_offset`` 是该块之前已经读过的数据行数（不含表头），
    用于把无效行定位到原始文件的行号。
    """
    df = _empty_if_missing(raw, RAW_REQUIRED_COLUMNS + RAW_OPTIONAL_COLUMNS)
    df = df.rename(
        columns={
            "product_id": "item_id",
            "event_type": "raw_event_type",
            "user_session": "session_id",
        }
    )
    df = df[list(dict.fromkeys(list(df.columns)))].copy()

    # 统一按字符串清理，保留 ID 的前导零和大整数
    text_cols = [
        "user_id",
        "item_id",
        "category_id",
        "category_code",
        "brand",
        "raw_event_type",
        "session_id",
        "event_time",
        "price",
    ]
    for col in text_cols:
        df[col] = df[col].astype("string").str.strip()

    raw_type = df["raw_event_type"].str.lower()
    behavior = raw_type.map(BEHAVIOR_MAP)
    event_time = parse_event_time(df["event_time"])
    price = pd.to_numeric(df["price"], errors="coerce").astype("float64")

    category_id = df["category_id"].where(df["category_id"].ne(""), "unknown")
    category_code = df["category_code"].where(df["category_code"].ne(""), pd.NA)
    brand = df["brand"].where(df["brand"].ne(""), pd.NA)
    session_id = df["session_id"].where(df["session_id"].ne(""), pd.NA)

    missing_user = df["user_id"].isna() | df["user_id"].eq("")
    missing_item = df["item_id"].isna() | df["item_id"].eq("")
    missing_time = df["event_time"].isna() | df["event_time"].eq("")
    invalid_time = event_time.isna() & ~missing_time
    unknown_behavior = behavior.isna()
    negative_price = price.lt(0).fillna(False)

    flags = {
        "missing_user": missing_user.fillna(False),
        "missing_item": missing_item.fillna(False),
        "missing_time": missing_time.fillna(False),
        "invalid_time": invalid_time.fillna(False),
        "unknown_behavior": unknown_behavior.fillna(False),
        "negative_price": negative_price,
    }
    invalid_mask = pd.Series(False, index=df.index)
    for mask in flags.values():
        invalid_mask = invalid_mask | mask

    valid_mask = ~invalid_mask
    out = pd.DataFrame(
        {
            "user_id": df.loc[valid_mask, "user_id"].astype("string"),
            "item_id": df.loc[valid_mask, "item_id"].astype("string"),
            "category_id": category_id.loc[valid_mask].astype("string"),
            "category_code": category_code.loc[valid_mask].astype("string"),
            "brand": brand.loc[valid_mask].astype("string"),
            "behavior_type": behavior.loc[valid_mask].astype("string"),
            "raw_event_type": raw_type.loc[valid_mask].astype("string"),
            "event_time": event_time.loc[valid_mask],
            "price": price.loc[valid_mask],
            "session_id": session_id.loc[valid_mask].astype("string"),
        }
    )
    out = out[CANONICAL_COLUMNS]

    before_dedupe = len(out)
    out = out.drop_duplicates(subset=DUPLICATE_SUBSET, keep="first")
    duplicates = before_dedupe - len(out)

    # 无效原因与样例（只处理无效行，避免整块 apply）
    invalid_reasons: dict[str, int] = {name: int(mask.sum()) for name, mask in flags.items()}
    examples: list[dict] = []
    if max_examples > 0 and invalid_mask.any():
        invalid_index = df.index[invalid_mask]
        reason_map: dict[int, list[str]] = defaultdict(list)
        for name, mask in flags.items():
            for idx in invalid_index[mask.loc[invalid_index].to_numpy()]:
                reason_map[idx].append(name)
        for idx in invalid_index[:max_examples]:
            pos = int(df.index.get_loc(idx))
            raw_row = raw.loc[idx].to_dict() if idx in raw.index else {}
            examples.append(
                {
                    "file": file_name,
                    "row_number": int(row_offset + pos + 2),  # 表头占第 1 行
                    "reasons": ";".join(reason_map.get(idx, [])),
                    "raw": {str(k): str(v) for k, v in raw_row.items()},
                }
            )

    # 统计信息
    valid_time = out["event_time"]
    price_values = out["price"].dropna()
    stats = {
        "rows": int(len(df)),
        "valid_before_dedupe": int(before_dedupe),
        "invalid": int(invalid_mask.sum()),
        "duplicates": int(duplicates),
        "kept": int(len(out)),
        "invalid_reasons": invalid_reasons,
        "raw_type_counts": {
            str(k): int(v)
            for k, v in raw_type.loc[valid_mask].value_counts(dropna=False).items()
        },
        "behavior_counts": {
            str(k): int(v) for k, v in out["behavior_type"].value_counts().items()
        },
        "missing_category_code": int(out["category_code"].isna().sum()),
        "missing_brand": int(out["brand"].isna().sum()),
        "missing_price": int(out["price"].isna().sum()),
        "zero_price": int(out["price"].eq(0).sum()),
        "negative_price": int(invalid_reasons["negative_price"]),
        "price_count": int(len(price_values)),
        "price_sum": float(price_values.sum()) if len(price_values) else 0.0,
        "price_min": float(price_values.min()) if len(price_values) else None,
        "price_max": float(price_values.max()) if len(price_values) else None,
        "time_min": valid_time.min() if valid_time.notna().any() else None,
        "time_max": valid_time.max() if valid_time.notna().any() else None,
        "users": out["user_id"],
        "items": out["item_id"],
        "categories": out["category_id"],
        "brands": out["brand"],
    }
    return out, stats, examples


def sample_user_mask(user_ids: pd.Series, per_mille: int, seed: int) -> pd.Series:
    """按用户稳定抽样：同一个 user_id 永远落在同一组。

    per_mille=10 表示约千分之十（1%）的用户。
    数字 ID 用取模，非数字 ID 用带种子的 blake2b，保证可复现。
    """
    if per_mille >= 1000:
        return pd.Series(True, index=user_ids.index)

    numeric = pd.to_numeric(user_ids, errors="coerce")
    bucket = numeric.mod(1000).lt(per_mille)
    non_numeric = numeric.isna()
    if non_numeric.any():
        def stable_bucket(uid: str) -> bool:
            digest = hashlib.blake2b(
                f"{seed}:{uid}".encode("utf-8"), digest_size=8
            ).digest()
            return int.from_bytes(digest, "big") % 1000 < per_mille

        bucket.loc[non_numeric] = (
            user_ids[non_numeric].map(stable_bucket).astype(bool)
        )
    return bucket.fillna(False)


def _to_arrow_table(df: pd.DataFrame) -> pa.Table:
    """把规范 DataFrame 转成固定 schema 的 Arrow 表。"""
    def str_array(col: str) -> pa.Array:
        values = df[col].astype(object).where(df[col].notna(), None)
        return pa.array(values, type=pa.string())

    return pa.Table.from_arrays(
        [
            str_array("user_id"),
            str_array("item_id"),
            str_array("category_id"),
            str_array("category_code"),
            str_array("brand"),
            str_array("behavior_type"),
            str_array("raw_event_type"),
            pa.array(df["event_time"], type=pa.timestamp("ns", tz=UTC)),
            pa.array(df["price"].astype("float64"), type=pa.float64()),
            str_array("session_id"),
        ],
        schema=SAMPLE_SCHEMA,
    )


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def build_sample(
    inputs: Sequence[str | Path],
    output_parquet: str | Path,
    *,
    quality_json: str | Path | None = None,
    quality_md: str | Path | None = None,
    sample_meta: str | Path | None = None,
    per_mille: int = 10,
    seed: int = 20261116,
    chunksize: int = 300_000,
    max_examples: int = 50,
    progress: bool = True,
) -> dict:
    """扫描原始 CSV，生成质量报告并按用户抽样。

    只读取一次原始文件，同时完成“质量扫描”和“抽样”，
    避免对 13GB 数据重复扫描。
    """
    input_paths = [Path(p) for p in inputs]
    output_parquet = Path(output_parquet)
    # 必须在 mkdir/unlink/打开 writer 之前检查，防止把输入文件当输出删掉
    ensure_distinct_paths(
        input_paths,
        [
            ("抽样 Parquet", output_parquet),
            ("质量报告 JSON", quality_json),
            ("质量报告 Markdown", quality_md),
            ("抽样元信息", sample_meta),
        ],
    )
    for path in input_paths:
        if not path.exists():
            raise FileNotFoundError(path)
        validate_header(path)

    # 先写独立临时文件，成功关闭后再原子替换最终输出
    temp_dir, temp_output = temp_output_path(output_parquet)
    writer = pq.ParquetWriter(temp_output, SAMPLE_SCHEMA, compression="zstd")

    quality = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [],
        "rows_scanned": 0,
        "valid_rows": 0,
        "invalid_rows": 0,
        "duplicate_rows": 0,
        "kept_rows": 0,
        "behavior_type_counts": {},
        "raw_event_type_counts": {},
        "invalid_reasons": {},
        "missing_counts": {
            "category_code": 0,
            "brand": 0,
            "price": 0,
        },
        "price": {
            "count": 0,
            "sum": 0.0,
            "min": None,
            "max": None,
            "zero": 0,
            "negative": 0,
        },
        "time_range": {"min": None, "max": None},
        "unique": {},
        "invalid_examples": [],
        "sample": {},
        "notes": [
            "重复行按完整字段组合在块内去重；跨块重复极少，加载样本时会再次去重。",
            "金额为购买事件单价汇总，每条购买事件按 1 件计，不等同于订单 GMV。",
            "remove_from_cart 单独保留，用于购物车放弃分析，不进入四类核心行为。",
            "category_code / brand 的缺失保持为空，不编造商品名或品牌。",
        ],
    }

    unique_users = BoundedSet(cap=2_000_000)
    unique_items = BoundedSet(cap=2_000_000)
    unique_categories = BoundedSet(cap=100_000)
    unique_brands = BoundedSet(cap=100_000)

    sample = {
        "per_mille": per_mille,
        "seed": seed,
        "rows": 0,
        "users": 0,
        "behavior_counts": {},
        "time_min": None,
        "time_max": None,
        "output": str(output_parquet),
    }
    sample_users: set[str] = set()
    sample_price_sum = 0.0
    sample_price_count = 0

    for path in input_paths:
        file_stats = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "rows": 0,
            "valid": 0,
            "invalid": 0,
            "duplicates": 0,
            "kept": 0,
        }
        offset = 0
        chunk_no = 0
        if progress:
            print(f"[scan] {path.name} ({file_stats['size_bytes'] / 1e9:.2f} GB) ...", flush=True)

        reader = pd.read_csv(
            path,
            dtype="string",
            keep_default_na=False,
            encoding="utf-8-sig",
            chunksize=chunksize,
            on_bad_lines="warn",
        )
        for raw in reader:
            chunk_no += 1
            remaining = max(0, max_examples - len(quality["invalid_examples"]))
            valid, stats, examples = normalize_chunk(
                raw,
                row_offset=offset,
                file_name=path.name,
                max_examples=remaining,
            )
            quality["invalid_examples"].extend(examples)

            # 全局质量统计
            quality["rows_scanned"] += stats["rows"]
            quality["valid_rows"] += stats["valid_before_dedupe"]
            quality["invalid_rows"] += stats["invalid"]
            quality["duplicate_rows"] += stats["duplicates"]
            quality["kept_rows"] += stats["kept"]
            _merge_counts(quality["behavior_type_counts"], stats["behavior_counts"])
            _merge_counts(quality["raw_event_type_counts"], stats["raw_type_counts"])
            _merge_counts(quality["invalid_reasons"], stats["invalid_reasons"])
            quality["missing_counts"]["category_code"] += stats["missing_category_code"]
            quality["missing_counts"]["brand"] += stats["missing_brand"]
            quality["missing_counts"]["price"] += stats["missing_price"]
            quality["price"]["count"] += stats["price_count"]
            quality["price"]["sum"] += stats["price_sum"]
            quality["price"]["zero"] += stats["zero_price"]
            quality["price"]["negative"] += stats["negative_price"]
            if stats["price_min"] is not None:
                current = quality["price"]["min"]
                quality["price"]["min"] = (
                    stats["price_min"] if current is None else min(current, stats["price_min"])
                )
            if stats["price_max"] is not None:
                current = quality["price"]["max"]
                quality["price"]["max"] = (
                    stats["price_max"] if current is None else max(current, stats["price_max"])
                )
            for key in ("time_min", "time_max"):
                value = stats[key]
                if value is not None:
                    current = quality["time_range"]["min" if key == "time_min" else "max"]
                    if key == "time_min":
                        quality["time_range"]["min"] = (
                            value if current is None else min(current, value)
                        )
                    else:
                        quality["time_range"]["max"] = (
                            value if current is None else max(current, value)
                        )

            if not valid.empty:
                unique_users.add_many(valid["user_id"])
                unique_items.add_many(valid["item_id"])
                unique_categories.add_many(valid["category_id"])
                unique_brands.add_many(valid["brand"])

            file_stats["rows"] += stats["rows"]
            file_stats["valid"] += stats["valid_before_dedupe"]
            file_stats["invalid"] += stats["invalid"]
            file_stats["duplicates"] += stats["duplicates"]
            file_stats["kept"] += stats["kept"]

            # 按用户抽样并写入 Parquet
            if not valid.empty:
                keep = sample_user_mask(valid["user_id"], per_mille, seed)
                selected = valid.loc[keep]
                if not selected.empty:
                    table = _to_arrow_table(selected)
                    writer.write_table(table)
                    sample["rows"] += len(selected)
                    sample_users.update(selected["user_id"].dropna().astype(str))
                    _merge_counts(
                        sample["behavior_counts"],
                        {
                            str(k): int(v)
                            for k, v in selected["behavior_type"].value_counts().items()
                        },
                    )
                    times = selected["event_time"]
                    if times.notna().any():
                        tmin, tmax = times.min(), times.max()
                        sample["time_min"] = (
                            tmin if sample["time_min"] is None else min(sample["time_min"], tmin)
                        )
                        sample["time_max"] = (
                            tmax if sample["time_max"] is None else max(sample["time_max"], tmax)
                        )
                    sample_price_sum += float(selected["price"].sum(skipna=True))
                    sample_price_count += int(selected["price"].notna().sum())

            offset += stats["rows"]

            if progress and chunk_no % 20 == 0:
                print(
                    f"  chunk {chunk_no}: rows={offset:,}, "
                    f"sample_rows={sample['rows']:,}, sample_users={len(sample_users):,}",
                    flush=True,
                )

        quality["inputs"].append(file_stats)
        if progress:
            print(
                f"[scan] {path.name} done: rows={file_stats['rows']:,}, "
                f"invalid={file_stats['invalid']:,}, duplicates={file_stats['duplicates']:,}",
                flush=True,
            )

    writer.close()
    os.replace(temp_output, output_parquet)
    shutil.rmtree(temp_dir, ignore_errors=True)

    sample["users"] = len(sample_users)
    sample["price_sum"] = round(sample_price_sum, 2)
    sample["price_count"] = sample_price_count
    sample["time_min"] = sample["time_min"].isoformat() if sample["time_min"] else None
    sample["time_max"] = sample["time_max"].isoformat() if sample["time_max"] else None
    sample["generated_at"] = datetime.now(timezone.utc).isoformat()

    quality["unique"] = {
        "users": unique_users.result(),
        "items": unique_items.result(),
        "categories": unique_categories.result(),
        "brands": unique_brands.result(),
    }
    quality["sample"] = sample
    quality["price"]["sum"] = round(quality["price"]["sum"], 2)
    for key in ("min", "max"):
        value = quality["time_range"][key]
        if value is not None:
            quality["time_range"][key] = value.isoformat()

    if quality_json:
        write_json(quality, quality_json)
    if sample_meta:
        write_json(sample, sample_meta)
    if quality_md:
        write_text(render_quality_markdown(quality), quality_md)

    return {"quality": quality, "sample": sample}


def render_quality_markdown(report: dict) -> str:
    """把质量报告渲染成 Markdown，方便贴到报告/群里。"""
    inputs = report["inputs"]
    total = report["rows_scanned"]
    valid = report["valid_rows"]
    invalid = report["invalid_rows"]
    dup = report["duplicate_rows"]
    kept = report["kept_rows"]
    behavior = report.get("behavior_type_counts", {})
    raw_types = report.get("raw_event_type_counts", {})
    missing = report.get("missing_counts", {})
    price = report.get("price", {})
    timerange = report.get("time_range", {})
    sample = report.get("sample", {})
    price_count = int(price.get("count", 0) or 0)
    price_sum = float(price.get("sum", 0) or 0)
    price_mean = f"{price_sum / price_count:.2f}" if price_count else "—"
    price_min = price.get("min")
    price_max = price.get("max")
    price_range = (
        f"{price_min if price_min is not None else '—'} 至 "
        f"{price_max if price_max is not None else '—'}"
    )

    def pct(n: int, d: int) -> str:
        return f"{n / d * 100:.2f}%" if d else "—"

    duplicate_scope = report.get("duplicate_scope", "chunk")
    if duplicate_scope == "sample_deduped":
        duplicate_line = "- 重复行：抽样写入时执行 SELECT DISTINCT（全量未逐行统计）"
        kept_line = f"- 全量有效行：{kept:,}"
    else:
        duplicate_line = f"- 块内重复行：{dup:,}（{pct(dup, total)}）"
        kept_line = f"- 去重后保留：{kept:,}"

    lines = [
        "# 数据质量报告",
        "",
        f"- 生成时间（UTC）：{report.get('generated_at', '—')}",
        f"- 输入文件：{', '.join(Path(i['path']).name for i in inputs)}",
        f"- 扫描总行数：{total:,}",
        f"- 有效行：{valid:,}（{pct(valid, total)}）",
        f"- 无效行：{invalid:,}（{pct(invalid, total)}）",
        duplicate_line,
        kept_line,
        f"- 时间范围（UTC）：{timerange.get('min', '—')} 至 {timerange.get('max', '—')}",
        "",
        "## 行为分布（有效行）",
        "",
    ]
    for key, value in sorted(behavior.items(), key=lambda x: -x[1]):
        lines.append(f"- `{key}`：{value:,}（{pct(value, kept)}）")
    lines += [
        "",
        "## 原始 event_type 分布",
        "",
    ]
    for key, value in sorted(raw_types.items(), key=lambda x: -x[1]):
        lines.append(f"- `{key}`：{value:,}")
    lines += [
        "",
        "## 缺失与异常",
        "",
        f"- `category_code` 缺失：{missing.get('category_code', 0):,}",
        f"- `brand` 缺失：{missing.get('brand', 0):,}",
        f"- `price` 缺失：{missing.get('price', 0):,}",
        f"- `price = 0`：{price.get('zero', 0):,}",
        f"- 负价格：{price.get('negative', 0):,}",
        f"- 价格范围：{price_range}，均值 {price_mean}",
        "",
        "## 唯一值（全量扫描）",
        "",
    ]
    for key, value in report.get("unique", {}).items():
        note = "（已截断）" if value.get("truncated") else ""
        lines.append(f"- {key}：{value.get('count', 0):,}{note}")
    lines += [
        "",
        "## 无效原因",
        "",
    ]
    for key, value in sorted(report.get("invalid_reasons", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {key}：{value:,}")
    lines += [
        "",
        "## 抽样结果",
        "",
        f"- 抽样比例：千分之 {sample.get('per_mille', '—')}（约 {sample.get('per_mille', 0) / 10:.2f}%）",
        f"- 抽样用户数：{sample.get('users', 0):,}",
        f"- 抽样行数：{sample.get('rows', 0):,}",
        f"- 抽样时间范围（UTC）：{sample.get('time_min', '—')} 至 {sample.get('time_max', '—')}",
        f"- 输出文件：`{sample.get('output', '—')}`",
        "",
        "## 口径说明",
        "",
    ]
    lines += [f"- {note}" for note in report.get("notes", [])]
    if report.get("invalid_examples"):
        lines += ["", "## 无效行样例（前几条）", ""]
        for ex in report["invalid_examples"][:10]:
            row_label = (
                f"第 {ex.get('row_number')} 行"
                if ex.get("row_number") is not None
                else "无行号"
            )
            lines.append(
                f"- `{ex.get('file')}` {row_label}："
                f"{ex.get('reasons')}；原始值 `{ex.get('raw')}`"
            )
    lines.append("")
    return "\n".join(lines)


def load_events(
    path: str | Path,
    *,
    dedupe: bool = True,
    sort: bool = True,
) -> pd.DataFrame:
    """加载抽样后的 Parquet/CSV，补齐分析所需的派生列。"""
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        raw = pd.read_csv(
            p,
            dtype="string",
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        df, _, _ = normalize_chunk(raw)
        if df.empty:
            raise ValueError(f"{p} 没有有效记录")

    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"缺少规范列: {col}")

    df = df.copy()
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True, errors="coerce")
    df = df.dropna(subset=["event_time"])

    for col in ["user_id", "item_id", "category_id", "category_code", "brand", "behavior_type", "raw_event_type", "session_id"]:
        df[col] = df[col].astype("string")
    df["price"] = pd.to_numeric(df["price"], errors="coerce").astype("float64")

    if dedupe:
        df = df.drop_duplicates(subset=DUPLICATE_SUBSET, keep="first")
    if sort:
        df = df.sort_values(["event_time", "user_id", "item_id"], kind="mergesort")

    df = df.reset_index(drop=True)
    df["event_time_cn"] = df["event_time"].dt.tz_convert(CN_TZ)
    df["date"] = df["event_time_cn"].dt.strftime("%Y-%m-%d")
    df["hour"] = df["event_time_cn"].dt.hour.astype("int8")
    df["event_ts"] = df["event_time"].astype("int64")
    return df
