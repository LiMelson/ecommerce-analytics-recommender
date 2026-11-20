"""导出网站标准入库文件。

网站契约见 ``docs/数据与算法交付约定.md``：
只接受 ``user_id,item_id,category_id,behavior_type,timestamp`` 五列，
行为只接受 pv/fav/cart/buy，timestamp 为 UTC 毫秒，未知类目留空。

完整训练文件（品牌、价格、会话等）仍由 ``build-sample`` 输出；
本模块只负责另生成一个可直接入库的视图。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .io_utils import write_json
from .path_utils import atomic_output, ensure_distinct_paths
from .schema import CORE_BEHAVIORS

WEBSITE_COLUMNS = ["user_id", "item_id", "category_id", "behavior_type", "timestamp"]
MIN_MS = 946684800000  # 2000-01-01
MAX_MS = 4102444800000  # 2100-01-01


def _validate_identifier(values: pd.Series, name: str, *, required: bool) -> None:
    text = values.fillna("").astype(str)
    empty = text.str.strip().eq("")
    if not required:
        empty = pd.Series(False, index=text.index)
    too_long = text.str.len() > 128
    control = text.map(lambda value: any(ord(char) < 32 for char in value))
    whitespace = text.ne(text.str.strip())
    bad = empty | too_long | control | whitespace
    if bad.any():
        examples = text[bad].head(5).tolist()
        raise ValueError(
            f"{name} 有 {int(bad.sum())} 条不符合网站入库要求"
            f"（空/超过 128 字符/控制字符/首尾空白），示例：{examples}"
        )


def export_website(
    df: pd.DataFrame,
    output_path: str | Path,
    *,
    source_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict:
    """把规范 DataFrame 导出为网站标准 CSV 或 Parquet。

    - 只保留 pv/fav/cart/buy；remove_from_cart 等排除并计数；
    - timestamp = UTC 毫秒（13 位）；
    - category_id 为 unknown/缺失时导出空字符串；
    - 输出列严格为五列，不接受额外字段。
    """
    output_path = Path(output_path)
    ensure_distinct_paths(
        [source_path] if source_path else [],
        [("网站导出文件", output_path), ("导出报告", report_path)],
    )

    source_rows = int(len(df))
    core_mask = df["behavior_type"].isin(CORE_BEHAVIORS)
    core = df.loc[core_mask]
    excluded = df.loc[~core_mask, "behavior_type"]
    if core.empty:
        raise ValueError("样本中没有可入库的 pv/fav/cart/buy 行为")

    event_time = pd.to_datetime(core["event_time"], utc=True, errors="coerce")
    if event_time.isna().any():
        raise ValueError("存在无法解析的 event_time，不能导出网站入库文件")
    timestamp = event_time.dt.as_unit("ms").astype("int64")
    if not timestamp.between(MIN_MS, MAX_MS - 1).all():
        raise ValueError("timestamp 超出 2000–2100，不能导出网站入库文件")

    user_id = core["user_id"].fillna("").astype(str)
    item_id = core["item_id"].fillna("").astype(str)
    category_id = core["category_id"].fillna("").astype(str)
    unknown_category = category_id.isin(["unknown", "<NA>", "nan", "None"])
    category_id = category_id.where(~unknown_category, "")

    _validate_identifier(user_id, "user_id", required=True)
    _validate_identifier(item_id, "item_id", required=True)
    _validate_identifier(category_id, "category_id", required=False)

    exported = pd.DataFrame(
        {
            "user_id": user_id.to_numpy(),
            "item_id": item_id.to_numpy(),
            "category_id": category_id.to_numpy(),
            "behavior_type": core["behavior_type"].astype(str).to_numpy(),
            "timestamp": timestamp.to_numpy(),
        }
    )

    with atomic_output(output_path) as temp_path:
        if output_path.suffix.lower() == ".parquet":
            exported.to_parquet(temp_path, index=False)
        else:
            exported.to_csv(
                temp_path,
                index=False,
                encoding="utf-8-sig",
                lineterminator="\n",
            )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output": str(output_path),
        "columns": WEBSITE_COLUMNS,
        "source_rows": source_rows,
        "exported_rows": int(len(exported)),
        "excluded_rows": int(len(excluded)),
        "excluded_by_type": {
            str(key): int(value)
            for key, value in excluded.value_counts().items()
        },
        "timestamp_unit": "milliseconds since epoch UTC",
        "timestamp_range": {
            "min": int(timestamp.min()) if len(timestamp) else None,
            "max": int(timestamp.max()) if len(timestamp) else None,
        },
        "unknown_category_rows": int(unknown_category.sum()),
        "format": "parquet" if output_path.suffix.lower() == ".parquet" else "csv",
        "note": (
            "网站入库只接受五列；remove_from_cart 等非核心行为只保留在完整训练文件中。"
            "未知类目导出为空，不编造。"
        ),
    }
    if report_path:
        write_json(report, report_path)
    return report
