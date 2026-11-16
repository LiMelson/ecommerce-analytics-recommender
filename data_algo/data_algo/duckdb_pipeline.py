"""DuckDB 快速扫描大 CSV。

pandas 版本适合小样本和单元测试；真实数据 1.1 亿行时，
DuckDB 可以在几分钟内完成全量质量统计 + 按用户抽样。

输出与 ``cleaning.build_sample`` 保持一致：
- 抽样 Parquet
- quality_report.json / .md
- sample_meta.json
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .cleaning import render_quality_markdown, validate_header
from .io_utils import write_json, write_text


def _q(value: str) -> str:
    """SQL 字符串转义。"""
    return "'" + str(value).replace("'", "''") + "'"


def _paths_sql(paths: Sequence[Path]) -> str:
    return "[" + ", ".join(_q(p.resolve().as_posix()) for p in paths) + "]"


def _create_views(con, inputs: Sequence[Path]) -> None:
    paths = _paths_sql(inputs)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW raw AS
        SELECT * FROM read_csv(
            {paths},
            header = true,
            all_varchar = true,
            filename = true
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW cleaned AS
        SELECT
            COALESCE(trim(user_id), '') AS user_id,
            COALESCE(trim(product_id), '') AS item_id,
            COALESCE(NULLIF(trim(category_id), ''), 'unknown') AS category_id,
            NULLIF(trim(category_code), '') AS category_code,
            NULLIF(trim(brand), '') AS brand,
            CASE lower(trim(event_type))
                WHEN 'view' THEN 'pv'
                WHEN 'pv' THEN 'pv'
                WHEN 'click' THEN 'pv'
                WHEN 'cart' THEN 'cart'
                WHEN 'addtocart' THEN 'cart'
                WHEN 'add_to_cart' THEN 'cart'
                WHEN 'purchase' THEN 'buy'
                WHEN 'buy' THEN 'buy'
                WHEN 'order' THEN 'buy'
                WHEN 'fav' THEN 'fav'
                WHEN 'favorite' THEN 'fav'
                WHEN 'collect' THEN 'fav'
                WHEN 'remove_from_cart' THEN 'remove_from_cart'
                WHEN 'remove' THEN 'remove_from_cart'
                ELSE NULL
            END AS behavior_type,
            lower(trim(event_type)) AS raw_event_type,
            TRY_STRPTIME(trim(event_time), '%Y-%m-%d %H:%M:%S UTC') AS event_time,
            TRY_CAST(NULLIF(trim(price), '') AS DOUBLE) AS price,
            NULLIF(trim(user_session), '') AS session_id,
            filename,
            COALESCE(trim(event_time), '') AS raw_event_time,
            COALESCE(trim(event_type), '') AS raw_event_type_original,
            COALESCE(trim(price), '') AS raw_price,
            COALESCE(trim(user_id), '') AS raw_user_id,
            COALESCE(trim(product_id), '') AS raw_item_id,
            (
                COALESCE(trim(user_id), '') <> ''
                AND COALESCE(trim(product_id), '') <> ''
                AND COALESCE(trim(event_time), '') <> ''
                AND TRY_STRPTIME(trim(event_time), '%Y-%m-%d %H:%M:%S UTC') IS NOT NULL
                AND CASE lower(trim(event_type))
                    WHEN 'view' THEN true WHEN 'pv' THEN true WHEN 'click' THEN true
                    WHEN 'cart' THEN true WHEN 'addtocart' THEN true WHEN 'add_to_cart' THEN true
                    WHEN 'purchase' THEN true WHEN 'buy' THEN true WHEN 'order' THEN true
                    WHEN 'fav' THEN true WHEN 'favorite' THEN true WHEN 'collect' THEN true
                    WHEN 'remove_from_cart' THEN true WHEN 'remove' THEN true
                    ELSE false END
                AND (TRY_CAST(NULLIF(trim(price), '') AS DOUBLE) IS NULL
                     OR TRY_CAST(NULLIF(trim(price), '') AS DOUBLE) >= 0)
            ) AS is_valid,
            concat_ws(';',
                CASE WHEN COALESCE(trim(user_id), '') = '' THEN 'missing_user' END,
                CASE WHEN COALESCE(trim(product_id), '') = '' THEN 'missing_item' END,
                CASE WHEN COALESCE(trim(event_time), '') = '' THEN 'missing_time' END,
                CASE WHEN COALESCE(trim(event_time), '') <> ''
                      AND TRY_STRPTIME(trim(event_time), '%Y-%m-%d %H:%M:%S UTC') IS NULL
                     THEN 'invalid_time' END,
                CASE WHEN CASE lower(trim(event_type))
                    WHEN 'view' THEN true WHEN 'pv' THEN true WHEN 'click' THEN true
                    WHEN 'cart' THEN true WHEN 'addtocart' THEN true WHEN 'add_to_cart' THEN true
                    WHEN 'purchase' THEN true WHEN 'buy' THEN true WHEN 'order' THEN true
                    WHEN 'fav' THEN true WHEN 'favorite' THEN true WHEN 'collect' THEN true
                    WHEN 'remove_from_cart' THEN true WHEN 'remove' THEN true
                    ELSE false END = false THEN 'unknown_behavior' END,
                CASE WHEN TRY_CAST(NULLIF(trim(price), '') AS DOUBLE) < 0
                     THEN 'negative_price' END
            ) AS invalid_reason
        FROM raw
        """
    )


def _quality(con, max_examples: int) -> dict:
    row = con.execute(
        """
        SELECT
            count(*) AS rows_scanned,
            count(*) FILTER (WHERE is_valid) AS valid_rows,
            count(*) FILTER (WHERE NOT is_valid) AS invalid_rows,
            count(*) FILTER (WHERE is_valid AND behavior_type = 'pv') AS pv,
            count(*) FILTER (WHERE is_valid AND behavior_type = 'fav') AS fav,
            count(*) FILTER (WHERE is_valid AND behavior_type = 'cart') AS cart,
            count(*) FILTER (WHERE is_valid AND behavior_type = 'buy') AS buy,
            count(*) FILTER (WHERE is_valid AND behavior_type = 'remove_from_cart') AS remove_from_cart,
            count(*) FILTER (WHERE is_valid AND category_code IS NULL) AS missing_category_code,
            count(*) FILTER (WHERE is_valid AND brand IS NULL) AS missing_brand,
            count(*) FILTER (WHERE is_valid AND price IS NULL) AS missing_price,
            count(*) FILTER (WHERE is_valid AND price = 0) AS zero_price,
            count(*) FILTER (WHERE price < 0) AS negative_price,
            count(price) FILTER (WHERE is_valid) AS price_count,
            sum(price) FILTER (WHERE is_valid) AS price_sum,
            min(price) FILTER (WHERE is_valid) AS price_min,
            max(price) FILTER (WHERE is_valid) AS price_max,
            min(event_time) FILTER (WHERE is_valid) AS time_min,
            max(event_time) FILTER (WHERE is_valid) AS time_max,
            count(DISTINCT CASE WHEN is_valid THEN user_id END) AS users,
            count(DISTINCT CASE WHEN is_valid THEN item_id END) AS items,
            count(DISTINCT CASE WHEN is_valid THEN category_id END) AS categories,
            count(DISTINCT CASE WHEN is_valid THEN brand END) AS brands
        FROM cleaned
        """
    ).fetchone()

    raw_types = con.execute(
        """
        SELECT COALESCE(raw_event_type, ''), count(*)
        FROM cleaned GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()

    per_file = con.execute(
        """
        SELECT filename,
               count(*) AS rows,
               count(*) FILTER (WHERE is_valid) AS valid,
               count(*) FILTER (WHERE NOT is_valid) AS invalid
        FROM cleaned GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    invalid_reasons: dict[str, int] = {}
    for reason, count in con.execute(
        """
        SELECT invalid_reason, count(*)
        FROM cleaned WHERE NOT is_valid
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall():
        for part in str(reason or "").split(";"):
            if part:
                invalid_reasons[part] = invalid_reasons.get(part, 0) + int(count)

    examples = []
    if max_examples > 0:
        examples = [
            {
                "file": str(row[0]),
                "row_number": None,
                "reasons": str(row[6] or ""),
                "raw": {
                    "user_id": row[1],
                    "product_id": row[2],
                    "event_type": row[3],
                    "event_time": row[4],
                    "price": row[5],
                },
            }
            for row in con.execute(
                f"""
                SELECT filename, raw_user_id, raw_item_id,
                       raw_event_type_original, raw_event_time, raw_price,
                       invalid_reason
                FROM cleaned WHERE NOT is_valid LIMIT {int(max_examples)}
                """
            ).fetchall()
        ]

    return {
        "row": row,
        "raw_types": raw_types,
        "per_file": per_file,
        "invalid_reasons": invalid_reasons,
        "examples": examples,
    }


def build_sample_duckdb(
    inputs: Sequence[str | Path],
    output_parquet: str | Path,
    *,
    quality_json: str | Path | None = None,
    quality_md: str | Path | None = None,
    sample_meta: str | Path | None = None,
    per_mille: int = 10,
    seed: int = 20261116,
    max_examples: int = 50,
    threads: int = 4,
    memory_limit: str = "4GB",
    progress: bool = True,
) -> dict:
    """用 DuckDB 扫描大 CSV，生成质量报告与按用户抽样。

    返回结构与 ``cleaning.build_sample`` 一致，方便 CLI/后端共用。
    """
    if not 1 <= per_mille <= 1000:
        raise ValueError("per_mille 必须在 1 到 1000 之间")
    if not 1 <= threads <= 32:
        raise ValueError("threads 必须在 1 到 32 之间")

    input_paths = [Path(p) for p in inputs]
    for path in input_paths:
        if not path.exists():
            raise FileNotFoundError(path)
        validate_header(path)

    output_parquet = Path(output_parquet)
    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    if output_parquet.exists():
        output_parquet.unlink()

    import duckdb

    con = duckdb.connect()
    con.execute(f"PRAGMA threads = {int(threads)}")
    con.execute(f"PRAGMA memory_limit = {_q(memory_limit)}")

    started = time.perf_counter()
    if progress:
        print("[duckdb] 创建 CSV / 清洗视图 ...", flush=True)
    _create_views(con, input_paths)

    if progress:
        print("[duckdb] 全量质量统计（约 1 分钟）...", flush=True)
    q = _quality(con, max_examples)
    rows_scanned, valid_rows, invalid_rows = q["row"][:3]
    if progress:
        print(
            f"[duckdb] 质量统计完成：{rows_scanned:,} 行，"
            f"有效 {valid_rows:,}，无效 {invalid_rows:,}",
            flush=True,
        )

    condition = f"""
        (
            (TRY_CAST(user_id AS BIGINT) IS NOT NULL
             AND TRY_CAST(user_id AS BIGINT) % 1000 < {int(per_mille)})
            OR
            (TRY_CAST(user_id AS BIGINT) IS NULL
             AND hash(user_id || ':' || CAST({int(seed)} AS VARCHAR)) % 1000 < {int(per_mille)})
        )
    """
    if progress:
        print("[duckdb] 写入抽样 Parquet（约 1 分钟）...", flush=True)
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT
                user_id, item_id, category_id, category_code, brand,
                behavior_type, raw_event_type, event_time, price, session_id
            FROM cleaned
            WHERE is_valid AND {condition}
            ORDER BY event_time, user_id, item_id
        ) TO {_q(output_parquet.resolve().as_posix())}
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    sample_row = con.execute(
        f"""
        SELECT
            count(*) AS rows,
            count(DISTINCT user_id) AS users,
            min(event_time) AS time_min,
            max(event_time) AS time_max,
            count(*) FILTER (WHERE behavior_type = 'pv') AS pv,
            count(*) FILTER (WHERE behavior_type = 'fav') AS fav,
            count(*) FILTER (WHERE behavior_type = 'cart') AS cart,
            count(*) FILTER (WHERE behavior_type = 'buy') AS buy,
            count(*) FILTER (WHERE behavior_type = 'remove_from_cart') AS remove_from_cart,
            count(price) AS price_count,
            sum(price) AS price_sum
        FROM read_parquet({_q(output_parquet.resolve().as_posix())})
        """
    ).fetchone()

    sample = {
        "engine": "duckdb",
        "per_mille": per_mille,
        "seed": seed,
        "rows": int(sample_row[0]),
        "users": int(sample_row[1]),
        "time_min": sample_row[2].isoformat() if sample_row[2] else None,
        "time_max": sample_row[3].isoformat() if sample_row[3] else None,
        "behavior_counts": {
            "pv": int(sample_row[4]),
            "fav": int(sample_row[5]),
            "cart": int(sample_row[6]),
            "buy": int(sample_row[7]),
            "remove_from_cart": int(sample_row[8]),
        },
        "price_count": int(sample_row[9]),
        "price_sum": round(float(sample_row[10] or 0), 2),
        "output": str(output_parquet),
        "dedupe": "SELECT DISTINCT（写入时去重）",
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 组装成与 pandas 版本一致的 quality 结构
    inputs_meta = []
    per_file_map = {Path(row[0]).name: row for row in q["per_file"]}
    for path in input_paths:
        matched = per_file_map.get(path.name)
        inputs_meta.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "rows": int(matched[1]) if matched else 0,
                "valid": int(matched[2]) if matched else 0,
                "invalid": int(matched[3]) if matched else 0,
                "duplicates": 0,
                "kept": int(matched[2]) if matched else 0,
            }
        )

    quality = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "duckdb",
        "inputs": inputs_meta,
        "rows_scanned": int(rows_scanned),
        "valid_rows": int(valid_rows),
        "invalid_rows": int(invalid_rows),
        "duplicate_rows": 0,
        "duplicate_scope": "sample_deduped",
        "kept_rows": int(valid_rows),
        "behavior_type_counts": {
            "pv": int(q["row"][3]),
            "fav": int(q["row"][4]),
            "cart": int(q["row"][5]),
            "buy": int(q["row"][6]),
            "remove_from_cart": int(q["row"][7]),
        },
        "raw_event_type_counts": {
            str(key): int(count) for key, count in q["raw_types"]
        },
        "invalid_reasons": q["invalid_reasons"],
        "missing_counts": {
            "category_code": int(q["row"][8]),
            "brand": int(q["row"][9]),
            "price": int(q["row"][10]),
        },
        "price": {
            "count": int(q["row"][13]),
            "sum": round(float(q["row"][14] or 0), 2),
            "min": None if q["row"][15] is None else float(q["row"][15]),
            "max": None if q["row"][16] is None else float(q["row"][16]),
            "zero": int(q["row"][11]),
            "negative": int(q["row"][12]),
        },
        "time_range": {
            "min": q["row"][17].isoformat() if q["row"][17] else None,
            "max": q["row"][18].isoformat() if q["row"][18] else None,
        },
        "unique": {
            "users": {"count": int(q["row"][19]), "truncated": False, "note": "精确计数"},
            "items": {"count": int(q["row"][20]), "truncated": False, "note": "精确计数"},
            "categories": {"count": int(q["row"][21]), "truncated": False, "note": "精确计数"},
            "brands": {"count": int(q["row"][22]), "truncated": False, "note": "精确计数"},
        },
        "invalid_examples": q["examples"],
        "sample": sample,
        "notes": [
            "全量质量统计由 DuckDB 完成，无效行、缺失值、价格异常均为精确计数。",
            "抽样以用户为单位，保留被抽中用户的完整历史；写入时执行 SELECT DISTINCT 去重。",
            "全量重复行未逐行统计；本次抽样写入时已去重，加载数据时还会再次去重。",
            "金额为购买事件单价汇总，每条购买事件按 1 件计，不等同于订单 GMV。",
            "category_code / brand 的缺失保持为空，不编造商品名或品牌。",
        ],
    }

    if quality_json:
        write_json(quality, quality_json)
    if sample_meta:
        write_json(sample, sample_meta)
    if quality_md:
        write_text(render_quality_markdown(quality), quality_md)

    if progress:
        print(
            f"[duckdb] 完成：抽样 {sample['rows']:,} 行 / "
            f"{sample['users']:,} 用户，用时 {sample['elapsed_seconds']}s",
            flush=True,
        )

    return {"quality": quality, "sample": sample}
