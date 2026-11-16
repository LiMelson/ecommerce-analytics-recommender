"""统计分析口径。

所有函数都接收 ``load_events`` 返回的规范 DataFrame，
返回可直接写 JSON 的 Python 字典（或供后端读取的 DataFrame）。

口径原则
--------
- 时间统一按北京时间（UTC+8）分日、分时；
- 活跃用户按 user_id 去重；
- 漏斗以“同一用户 + 同一商品”为单位，要求时间顺序为 浏览 -> 加购 -> 购买；
- R/F/M：没有购买的 R 为 None，M 按购买事件单价汇总，不是订单 GMV；
- 留存是“样本首现留存”，不是注册留存；窗口未覆盖的日期返回 None，已覆盖未回访返回 0；
- 排行按购买事件聚合，购买用户数独立去重，数量相同按 ID 稳定排序。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .io_utils import to_native, write_json
from .schema import (
    BEHAVIOR_LABELS_ZH,
    CORE_BEHAVIORS,
    DEFAULT_HIGH_FREQUENCY,
    DEFAULT_RECENT_DAYS,
    INTEREST_WEIGHTS,
    PRICE_BIN_LABELS,
    PRICE_BINS,
    category_label,
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _sum_or_none(series: pd.Series) -> float | None:
    """忽略缺失值求和；全是缺失时返回 None，而不是 0。"""
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.sum()) if len(values) else None


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    """安全除法，分母为 0 或缺失时返回 None。"""
    if denominator is None or numerator is None:
        return None
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _series_count(series: pd.Series, keys: list[str]) -> dict[str, int]:
    counts = series.value_counts()
    return {key: int(counts.get(key, 0)) for key in keys}


# ---------------------------------------------------------------------------
# 概览 / 每日 / 每时
# ---------------------------------------------------------------------------

def overview(df: pd.DataFrame) -> dict:
    """概览、每日趋势、每时趋势一次算完。"""
    if df.empty:
        return {
            "total_events": 0,
            "users": 0,
            "items": 0,
            "categories": 0,
            "brands": 0,
            "behavior_counts": {},
            "purchase_amount": None,
            "date_range": {"min": None, "max": None},
            "daily": [],
            "hourly": [],
            "timezone": "Asia/Shanghai",
        }

    behaviors = list(CORE_BEHAVIORS) + ["remove_from_cart"]
    counts = _series_count(df["behavior_type"], behaviors)
    buys = df[df["behavior_type"] == "buy"]
    purchase_amount = _sum_or_none(buys["price"])

    # 每日聚合（北京时间）
    daily_counts = pd.crosstab(df["date"], df["behavior_type"])
    daily_counts = daily_counts.reindex(columns=behaviors, fill_value=0)
    daily_users = df.groupby("date")["user_id"].nunique()
    daily_amount = buys.groupby("date")["price"].sum(min_count=1)

    daily = []
    for day in sorted(df["date"].dropna().unique()):
        row = {"date": str(day)}
        for behavior in behaviors:
            row[behavior] = int(daily_counts.loc[day, behavior]) if day in daily_counts.index else 0
        row["users"] = int(daily_users.get(day, 0))
        amount = daily_amount.get(day, np.nan)
        row["purchase_amount"] = None if pd.isna(amount) else float(amount)
        daily.append(row)

    # 每时聚合（北京时间，0-23 点，跨天汇总）
    hourly_counts = pd.crosstab(df["hour"], df["behavior_type"])
    hourly_counts = hourly_counts.reindex(index=range(24), columns=behaviors, fill_value=0)
    hourly_users = df.groupby("hour")["user_id"].nunique()
    hourly_amount = buys.groupby("hour")["price"].sum(min_count=1)
    hourly = []
    for hour in range(24):
        row = {"hour": hour}
        for behavior in behaviors:
            row[behavior] = int(hourly_counts.loc[hour, behavior])
        row["users"] = int(hourly_users.get(hour, 0))
        amount = hourly_amount.get(hour, np.nan)
        row["purchase_amount"] = None if pd.isna(amount) else float(amount)
        hourly.append(row)

    return {
        "total_events": int(len(df)),
        "users": int(df["user_id"].nunique()),
        "items": int(df["item_id"].nunique()),
        "categories": int(df["category_id"].nunique()),
        "brands": int(df["brand"].nunique()),
        "behavior_counts": counts,
        "purchase_events": int(counts.get("buy", 0)),
        "purchase_amount": purchase_amount,
        "amount_caliber": "购买事件单价汇总，每条购买事件按 1 件计，不等同订单 GMV",
        "date_range": {
            "min": str(df["date"].min()),
            "max": str(df["date"].max()),
        },
        "time_range_utc": {
            "min": df["event_time"].min().isoformat(),
            "max": df["event_time"].max().isoformat(),
        },
        "timezone": "Asia/Shanghai",
        "daily": daily,
        "hourly": hourly,
    }


# ---------------------------------------------------------------------------
# 顺序漏斗
# ---------------------------------------------------------------------------

def conversion_funnel(df: pd.DataFrame) -> dict:
    """同一用户 + 同一商品，按时间识别 浏览 -> 加购 -> 购买。

    规则：
    - 每个用户商品对最多计一次；
    - 先购买后加购不构成完成路径；
    - 收藏不是必经阶段；
    - 没有可靠事件序号时，同一时间戳不推断先后（这里按稳定排序处理）。
    """
    stages = {"pv": 0, "cart": 0, "buy": 0}
    pairs = 0
    if df.empty:
        return {"stages": [], "pairs": 0, "note": "没有数据"}

    relevant = df[df["behavior_type"].isin(["pv", "cart", "buy"])].copy()
    if relevant.empty:
        return {"stages": [], "pairs": 0, "note": "没有浏览/加购/购买事件"}

    relevant = relevant.sort_values(
        ["user_id", "item_id", "event_time", "event_ts"],
        kind="mergesort",
    )

    for _, group in relevant.groupby(["user_id", "item_id"], sort=False):
        pairs += 1
        state = 0
        for behavior in group["behavior_type"].to_numpy():
            if state == 0 and behavior == "pv":
                state = 1
            elif state == 1 and behavior == "cart":
                state = 2
            elif state == 2 and behavior == "buy":
                state = 3
                break
        if state >= 1:
            stages["pv"] += 1
        if state >= 2:
            stages["cart"] += 1
        if state >= 3:
            stages["buy"] += 1

    pv, cart, buy = stages["pv"], stages["cart"], stages["buy"]
    return {
        "pairs": int(pairs),
        "stages": [
            {"key": "pv", "name": "浏览商品", "value": int(pv)},
            {"key": "cart", "name": "浏览后加购", "value": int(cart)},
            {"key": "buy", "name": "加购后购买", "value": int(buy)},
        ],
        "conversion_rates": {
            "pv_to_cart": _round(_rate(cart, pv)),
            "cart_to_buy": _round(_rate(buy, cart)),
            "pv_to_buy": _round(_rate(buy, pv)),
        },
        "note": "同用户同商品的顺序路径；先购买后加购不计完成；与首页行为量口径不同",
    }


def cart_abandonment(df: pd.DataFrame) -> dict | None:
    """购物车放弃分析。

    本数据集在 10-11 月没有 remove_from_cart，但有 cart 和 buy，
    因此仍然可以按“有加购、无购买”判断会话/用户商品是否放弃；
    如果有 remove_from_cart，会额外统计“加购后又移除”的会话。
    """
    if df.empty:
        return None
    if not (df["behavior_type"] == "cart").any():
        return None

    relevant = df[df["behavior_type"].isin(["cart", "buy", "remove_from_cart"])]
    if relevant.empty:
        return None

    session_flags = relevant.groupby("session_id", dropna=True).agg(
        has_cart=("behavior_type", lambda s: bool((s == "cart").any())),
        has_buy=("behavior_type", lambda s: bool((s == "buy").any())),
        has_remove=("behavior_type", lambda s: bool((s == "remove_from_cart").any())),
    )
    cart_sessions = int(session_flags["has_cart"].sum())
    converted_sessions = int((session_flags["has_cart"] & session_flags["has_buy"]).sum())
    abandoned_sessions = int((session_flags["has_cart"] & ~session_flags["has_buy"]).sum())
    removed_sessions = int((session_flags["has_cart"] & session_flags["has_remove"]).sum())

    pair_flags = df[df["behavior_type"].isin(["cart", "buy"])].groupby(
        ["user_id", "item_id"]
    ).agg(
        has_cart=("behavior_type", lambda s: bool((s == "cart").any())),
        has_buy=("behavior_type", lambda s: bool((s == "buy").any())),
    )
    pair_cart = int(pair_flags["has_cart"].sum())
    pair_abandoned = int((pair_flags["has_cart"] & ~pair_flags["has_buy"]).sum())

    return {
        "sessions": {
            "cart_sessions": cart_sessions,
            "converted_sessions": converted_sessions,
            "abandoned_sessions": abandoned_sessions,
            "abandonment_rate": _round(_rate(abandoned_sessions, cart_sessions)),
            "sessions_with_removal": removed_sessions,
        },
        "user_item_pairs": {
            "cart_pairs": pair_cart,
            "abandoned_pairs": pair_abandoned,
            "abandonment_rate": _round(_rate(pair_abandoned, pair_cart)),
        },
        "note": (
            "按会话是否存在加购/购买事件统计；没有 remove_from_cart 时"
            "只判断加购后是否购买；不是严格顺序归因"
        ),
    }


# ---------------------------------------------------------------------------
# RFM
# ---------------------------------------------------------------------------

def rfm_table(
    df: pd.DataFrame,
    *,
    reference_time: pd.Timestamp | None = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    high_frequency: int = DEFAULT_HIGH_FREQUENCY,
) -> tuple[pd.DataFrame, dict]:
    """计算 R / F / M 与分群。

    - R：距参考时刻最近一次购买的天数，没有购买为 None；
    - F：购买事件数，没有购买为 0；
    - M：购买事件单价汇总（每条按 1 件计），不是订单金额；
    - 阈值可配置，因为这份数据跨 2 个月，不适合文档里针对 9 天样本的 2 天/12 次。
    """
    reference = reference_time or df["event_time"].max()
    users = df[["user_id"]].drop_duplicates().copy()
    buys = df[df["behavior_type"] == "buy"].copy()

    if buys.empty:
        rfm = users.copy()
        rfm["last_buy_time"] = pd.NaT
        rfm["frequency"] = 0
        rfm["monetary"] = None
        rfm["recency_days"] = None
    else:
        agg = buys.groupby("user_id").agg(
            last_buy_time=("event_time", "max"),
            frequency=("event_time", "size"),
            monetary=("price", _sum_or_none),
        )
        rfm = users.merge(agg, left_on="user_id", right_index=True, how="left")
        rfm["frequency"] = rfm["frequency"].fillna(0).astype(int)
        delta_days = (reference - rfm["last_buy_time"]).dt.total_seconds() / 86400
        # 用 object 保存，确保“没有购买”是 None 而不是 NaN
        rfm["recency_days"] = pd.Series(
            [int(value) if pd.notna(value) else None for value in delta_days],
            index=rfm.index,
            dtype=object,
        )
        rfm["monetary"] = pd.Series(
            [None if pd.isna(value) else float(value) for value in rfm["monetary"]],
            index=rfm.index,
            dtype=object,
        )

    def segment(row: pd.Series) -> str:
        frequency = int(row["frequency"])
        recency = row["recency_days"]
        if frequency == 0:
            return "潜在转化用户"
        if recency is not None and recency <= recent_days:
            return "高频活跃用户" if frequency >= high_frequency else "近期购买用户"
        return "高频待唤回用户" if frequency >= high_frequency else "低频待唤回用户"

    rfm["segment"] = rfm.apply(segment, axis=1)
    rfm = rfm.sort_values(
        ["frequency", "monetary", "user_id"],
        ascending=[False, False, True],
        na_position="last",
    ).reset_index(drop=True)

    summary = {
        "reference_time": reference.isoformat(),
        "timezone": "Asia/Shanghai",
        "thresholds": {
            "recent_days": recent_days,
            "high_frequency": high_frequency,
            "note": "阈值针对当前抽样窗口，可在验证集/分位数上调整",
        },
        "users": int(len(rfm)),
        "buyers": int((rfm["frequency"] > 0).sum()),
        "non_buyers": int((rfm["frequency"] == 0).sum()),
        "segment_counts": {
            str(k): int(v) for k, v in rfm["segment"].value_counts().items()
        },
        "monetary_caliber": "购买事件单价汇总，每条按 1 件计，不是订单 GMV",
        "m_missing_note": "没有购买或没有有效价格时 M 为 null，不用 0 或虚构金额填充",
    }
    return rfm, summary


# ---------------------------------------------------------------------------
# 用户画像
# ---------------------------------------------------------------------------

def _top_category_table(core: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """按用户汇总类目兴趣，返回每个用户 Top N。"""
    if core.empty:
        return pd.DataFrame()
    grouped = (
        core.groupby(["user_id", "category_id"], observed=True)
        .agg(interest=("interest", "sum"), events=("event_time", "size"))
        .reset_index()
    )
    grouped["score"] = (
        grouped["interest"] / grouped.groupby("user_id")["interest"].transform("max") * 100
    ).round(1)
    grouped["share"] = (
        grouped["interest"] / grouped.groupby("user_id")["interest"].transform("sum") * 100
    ).round(1)
    grouped = grouped.sort_values(
        ["user_id", "interest", "events", "category_id"],
        ascending=[True, False, False, True],
    )
    return grouped.groupby("user_id").head(top_n).reset_index(drop=True)


def _top_brand_table(core: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    if core.empty:
        return pd.DataFrame()
    brands = core.dropna(subset=["brand"]).copy()
    if brands.empty:
        return pd.DataFrame()
    grouped = (
        brands.groupby(["user_id", "brand"], observed=True)
        .agg(interest=("interest", "sum"), events=("event_time", "size"))
        .reset_index()
    )
    grouped["score"] = (
        grouped["interest"] / grouped.groupby("user_id")["interest"].transform("max") * 100
    ).round(1)
    grouped["share"] = (
        grouped["interest"] / grouped.groupby("user_id")["interest"].transform("sum") * 100
    ).round(1)
    grouped = grouped.sort_values(
        ["user_id", "interest", "events", "brand"],
        ascending=[True, False, False, True],
    )
    return grouped.groupby("user_id").head(top_n).reset_index(drop=True)


def profiles_table(
    df: pd.DataFrame,
    *,
    rfm: pd.DataFrame | None = None,
    reference_time: pd.Timestamp | None = None,
    half_life_days: float = 3.0,
) -> tuple[pd.DataFrame, dict]:
    """生成每个用户的行为画像（标量字段，方便写 Parquet / 后端查询）。"""
    reference = reference_time or df["event_time"].max()
    core = df[df["behavior_type"].isin(["pv", "fav", "cart", "buy"])].copy()
    age_days = (reference - core["event_time"]).dt.total_seconds() / 86400
    core["interest"] = core["behavior_type"].map(INTEREST_WEIGHTS) * (
        0.5 ** (age_days / half_life_days)
    )

    users = df[["user_id"]].drop_duplicates().copy()

    # 行为次数
    behavior_counts = pd.crosstab(df["user_id"], df["behavior_type"])
    for behavior in list(CORE_BEHAVIORS) + ["remove_from_cart"]:
        if behavior not in behavior_counts.columns:
            behavior_counts[behavior] = 0
    behavior_counts = behavior_counts[
        [b for b in list(CORE_BEHAVIORS) + ["remove_from_cart"] if b in behavior_counts.columns]
    ]
    profiles = users.merge(
        behavior_counts.reset_index(), on="user_id", how="left"
    ).fillna({b: 0 for b in behavior_counts.columns})

    # 基础计数
    base = df.groupby("user_id").agg(
        event_count=("event_time", "size"),
        item_count=("item_id", "nunique"),
        category_count=("category_id", "nunique"),
        brand_count=("brand", "nunique"),
        session_count=("session_id", "nunique"),
        last_event_time=("event_time", "max"),
    )
    profiles = profiles.merge(base, left_on="user_id", right_index=True, how="left")

    # 类目 Top1
    cat = _top_category_table(core, top_n=5)
    if not cat.empty:
        code_map = (
            df.dropna(subset=["category_code"])
            .drop_duplicates("category_id")
            .set_index("category_id")["category_code"]
        )
        cat["category_code"] = cat["category_id"].map(code_map)
        cat["label"] = cat["category_code"].apply(category_label)
        top_cat = cat.sort_values(
            ["user_id", "interest", "events", "category_id"],
            ascending=[True, False, False, True],
        ).drop_duplicates("user_id")
        top_cat = top_cat.rename(
            columns={
                "category_id": "top_category_id",
                "category_code": "top_category_code",
                "label": "top_category_label",
                "interest": "top_category_interest",
                "score": "top_category_score",
                "share": "top_category_share",
                "events": "top_category_events",
            }
        )[
            [
                "user_id",
                "top_category_id",
                "top_category_code",
                "top_category_label",
                "top_category_interest",
                "top_category_score",
                "top_category_share",
                "top_category_events",
            ]
        ]
        profiles = profiles.merge(top_cat, on="user_id", how="left")

    # 品牌 Top1
    brand = _top_brand_table(core, top_n=5)
    if not brand.empty:
        top_brand = brand.sort_values(
            ["user_id", "interest", "events", "brand"],
            ascending=[True, False, False, True],
        ).drop_duplicates("user_id")
        top_brand = top_brand.rename(
            columns={
                "brand": "top_brand",
                "interest": "top_brand_interest",
                "score": "top_brand_score",
                "share": "top_brand_share",
                "events": "top_brand_events",
            }
        )[
            [
                "user_id",
                "top_brand",
                "top_brand_interest",
                "top_brand_score",
                "top_brand_share",
                "top_brand_events",
            ]
        ]
        profiles = profiles.merge(top_brand, on="user_id", how="left")

    # 价格带 Top1
    buys = df[df["behavior_type"] == "buy"].dropna(subset=["price"])
    if not buys.empty:
        buys = buys.copy()
        buys["price_band"] = pd.cut(
            buys["price"], bins=PRICE_BINS, labels=PRICE_BIN_LABELS, right=False
        )
        band_counts = (
            buys.dropna(subset=["price_band"])
            .groupby(["user_id", "price_band"], observed=True)
            .size()
            .reset_index(name="count")
        )
        if not band_counts.empty:
            band_counts = band_counts.sort_values(
                ["user_id", "count", "price_band"],
                ascending=[True, False, True],
            ).drop_duplicates("user_id")
            band_counts = band_counts.rename(
                columns={"price_band": "top_price_band", "count": "top_price_band_events"}
            )
            profiles = profiles.merge(band_counts, on="user_id", how="left")

    # RFM
    if rfm is not None:
        profiles = profiles.merge(
            rfm[
                [
                    "user_id",
                    "recency_days",
                    "frequency",
                    "monetary",
                    "last_buy_time",
                    "segment",
                ]
            ],
            on="user_id",
            how="left",
        )

    profiles = profiles.sort_values(
        ["event_count", "user_id"], ascending=[False, True]
    ).reset_index(drop=True)

    summary = {
        "users": int(len(profiles)),
        "half_life_days": half_life_days,
        "reference_time": reference.isoformat(),
        "segment_counts": (
            {str(k): int(v) for k, v in profiles["segment"].value_counts().items()}
            if "segment" in profiles.columns
            else {}
        ),
        "top_users": profiles.head(20)[
            [
                c
                for c in [
                    "user_id",
                    "event_count",
                    "item_count",
                    "top_category_label",
                    "top_brand",
                    "top_price_band",
                    "segment",
                ]
                if c in profiles.columns
            ]
        ].to_dict(orient="records"),
        "interest_method": "行为权重 × 时间衰减（半衰期可配置）后按类目/品牌汇总",
        "caveats": [
            "没有年龄、性别、城市等字段，不推断人口属性",
            "top_brand / top_price_band 缺失时保持 null，不编造",
        ],
    }
    return profiles, summary


def user_profile(
    df: pd.DataFrame,
    user_id: str,
    *,
    rfm: pd.DataFrame | None = None,
    reference_time: pd.Timestamp | None = None,
    half_life_days: float = 3.0,
    top_n: int = 5,
) -> dict | None:
    """查询单个用户的完整画像（供前端“查看画像”弹窗使用）。"""
    user_df = df[df["user_id"] == str(user_id)].copy()
    if user_df.empty:
        return None

    reference = reference_time or df["event_time"].max()
    core = user_df[user_df["behavior_type"].isin(["pv", "fav", "cart", "buy"])].copy()
    if not core.empty:
        age_days = (reference - core["event_time"]).dt.total_seconds() / 86400
        core["interest"] = core["behavior_type"].map(INTEREST_WEIGHTS) * (
            0.5 ** (age_days / half_life_days)
        )
    cat = _top_category_table(core, top_n=top_n)
    brand = _top_brand_table(core, top_n=top_n)

    code_map = (
        df.dropna(subset=["category_code"])
        .drop_duplicates("category_id")
        .set_index("category_id")["category_code"]
    )
    categories = []
    for row in cat.to_dict(orient="records"):
        code = code_map.get(row["category_id"])
        categories.append(
            {
                "category_id": str(row["category_id"]),
                "category_code": None if pd.isna(code) else str(code),
                "label": category_label(None if pd.isna(code) else str(code)),
                "interest": float(row["interest"]),
                "score": float(row["score"]),
                "share": float(row["share"]),
                "events": int(row["events"]),
            }
        )
    brands = [
        {
            "brand": str(row["brand"]),
            "interest": float(row["interest"]),
            "score": float(row["score"]),
            "share": float(row["share"]),
            "events": int(row["events"]),
        }
        for row in brand.to_dict(orient="records")
    ]

    buys = user_df[user_df["behavior_type"] == "buy"].dropna(subset=["price"]).copy()
    price_bands = []
    if not buys.empty:
        buys["price_band"] = pd.cut(
            buys["price"], bins=PRICE_BINS, labels=PRICE_BIN_LABELS, right=False
        )
        counts = buys["price_band"].value_counts().sort_index()
        price_bands = [
            {"band": str(band), "events": int(count)} for band, count in counts.items()
        ]

    recent = user_df.sort_values(["event_time", "event_ts"], ascending=False).head(10)
    recent_events = [
        {
            "item_id": str(row["item_id"]),
            "category_id": str(row["category_id"]),
            "category_code": None if pd.isna(row["category_code"]) else str(row["category_code"]),
            "brand": None if pd.isna(row["brand"]) else str(row["brand"]),
            "behavior_type": str(row["behavior_type"]),
            "event_time": row["event_time"].isoformat(),
            "event_time_cn": row["event_time_cn"].isoformat(),
            "price": None if pd.isna(row["price"]) else float(row["price"]),
        }
        for row in recent.to_dict(orient="records")
    ]

    behavior_counts = {
        str(k): int(v)
        for k, v in user_df["behavior_type"].value_counts().items()
    }
    profile = {
        "user_id": str(user_id),
        "event_count": int(len(user_df)),
        "item_count": int(user_df["item_id"].nunique()),
        "category_count": int(user_df["category_id"].nunique()),
        "brand_count": int(user_df["brand"].nunique()),
        "session_count": int(user_df["session_id"].nunique()),
        "behavior_counts": behavior_counts,
        "first_event_time": user_df["event_time"].min().isoformat(),
        "last_event_time": user_df["event_time"].max().isoformat(),
        "top_categories": categories,
        "top_brands": brands,
        "price_bands": price_bands,
        "recent_events": recent_events,
        "half_life_days": half_life_days,
        "reference_time": reference.isoformat(),
    }

    if rfm is not None:
        row = rfm[rfm["user_id"] == str(user_id)]
        if not row.empty:
            record = row.iloc[0].to_dict()
            profile["rfm"] = {
                "recency_days": None
                if pd.isna(record.get("recency_days"))
                else int(record["recency_days"]),
                "frequency": int(record.get("frequency", 0)),
                "monetary": None
                if pd.isna(record.get("monetary"))
                else float(record["monetary"]),
                "segment": str(record.get("segment", "")),
                "last_buy_time": None
                if pd.isna(record.get("last_buy_time"))
                else pd.Timestamp(record["last_buy_time"]).isoformat(),
            }
    return profile


# ---------------------------------------------------------------------------
# 留存
# ---------------------------------------------------------------------------

def retention(df: pd.DataFrame, *, max_day: int = 7) -> dict:
    """按样本首现日期分 cohort，计算第 0..max_day 天留存。

    - 窗口尚未覆盖的日期返回 None（前端显示“—”）
    - 已覆盖但无人回访返回 0
    - 这是“样本首现留存”，不是注册留存
    """
    if df.empty:
        return {"cohorts": [], "note": "没有数据"}

    active = df[["user_id", "date"]].drop_duplicates().copy()
    active["date_dt"] = pd.to_datetime(active["date"])
    first = (
        active.groupby("user_id")["date_dt"]
        .min()
        .reset_index(name="first_date")
    )
    active = active.merge(first, on="user_id", how="left")
    active["offset"] = (active["date_dt"] - active["first_date"]).dt.days

    cohorts = (
        active.drop_duplicates("user_id")
        .groupby("first_date")["user_id"]
        .nunique()
        .sort_index()
    )
    counts = active.groupby(["first_date", "offset"])["user_id"].nunique()
    max_date = active["date_dt"].max()

    rows = []
    for first_date, size in cohorts.items():
        values: list[float | None] = []
        for offset in range(max_day + 1):
            target = first_date + pd.Timedelta(days=offset)
            if target > max_date:
                values.append(None)
            else:
                values.append(int(counts.get((first_date, offset), 0)) / int(size))
        rows.append(
            {
                "day": first_date.strftime("%Y-%m-%d"),
                "users": int(size),
                "retention": values,
            }
        )

    # 加权平均（只统计已经覆盖的 cohort）
    def weighted(offset: int) -> float | None:
        total = 0
        weighted_sum = 0.0
        for row in rows:
            value = row["retention"][offset]
            if value is None:
                continue
            total += row["users"]
            weighted_sum += row["users"] * value
        return weighted_sum / total if total else None

    return {
        "cohorts": rows,
        "cohort_count": len(rows),
        "day1_average": _round(weighted(1)),
        "day7_average": _round(weighted(7)),
        "max_day": max_day,
        "timezone": "Asia/Shanghai",
        "note": "样本首现留存，不是注册留存；null 表示观察窗口尚未覆盖，0 表示已覆盖但无人回访",
    }


# ---------------------------------------------------------------------------
# 购买排行
# ---------------------------------------------------------------------------

def rankings(df: pd.DataFrame, *, top_n: int = 20) -> dict:
    """按购买事件聚合类目 / 品牌 / 商品排行。"""
    buys = df[df["behavior_type"] == "buy"].copy()
    if buys.empty:
        return {
            "buy_events": 0,
            "amount": None,
            "categories": [],
            "brands": [],
            "items": [],
            "note": "没有购买事件，无法生成购买排行",
        }

    total_events = int(len(buys))
    total_amount = _sum_or_none(buys["price"])

    def amount_share(amount: float | None) -> float | None:
        if amount is None or total_amount is None or total_amount == 0:
            return None
        return _round(amount / total_amount)

    # 类目
    cat = buys.groupby("category_id", observed=True).agg(
        count=("event_time", "size"),
        users=("user_id", "nunique"),
        amount=("price", _sum_or_none),
        category_code=("category_code", lambda s: next((v for v in s if pd.notna(v)), None)),
    ).reset_index()
    cat["label"] = cat["category_code"].apply(category_label)
    cat["event_share"] = (cat["count"] / total_events).round(4)
    cat["amount_share"] = cat["amount"].apply(amount_share)
    cat = cat.sort_values(["count", "category_id"], ascending=[False, True]).head(top_n)

    # 品牌
    brand_buys = buys.dropna(subset=["brand"])
    brand = brand_buys.groupby("brand", observed=True).agg(
        count=("event_time", "size"),
        users=("user_id", "nunique"),
        amount=("price", _sum_or_none),
    ).reset_index()
    brand["event_share"] = (brand["count"] / total_events).round(4)
    brand["amount_share"] = brand["amount"].apply(amount_share)
    brand = brand.sort_values(["count", "brand"], ascending=[False, True]).head(top_n)

    # 商品
    item = buys.groupby("item_id", observed=True).agg(
        count=("event_time", "size"),
        users=("user_id", "nunique"),
        amount=("price", _sum_or_none),
        category_code=("category_code", lambda s: next((v for v in s if pd.notna(v)), None)),
        brand=("brand", lambda s: next((v for v in s if pd.notna(v)), None)),
    ).reset_index()
    item["event_share"] = (item["count"] / total_events).round(4)
    item["amount_share"] = item["amount"].apply(amount_share)
    item = item.sort_values(["count", "item_id"], ascending=[False, True]).head(top_n)

    def records(frame: pd.DataFrame, key: str) -> list[dict]:
        out = []
        for row in frame.to_dict(orient="records"):
            out.append(
                {
                    key: str(row[key]),
                    "label": row.get("label"),
                    "category_code": None
                    if pd.isna(row.get("category_code"))
                    else row.get("category_code"),
                    "brand": None if pd.isna(row.get("brand")) else row.get("brand"),
                    "count": int(row["count"]),
                    "users": int(row["users"]),
                    "amount": None if pd.isna(row.get("amount")) else float(row["amount"]),
                    "event_share": None if pd.isna(row.get("event_share")) else float(row["event_share"]),
                    "amount_share": None if pd.isna(row.get("amount_share")) else float(row["amount_share"]),
                }
            )
        return out

    return {
        "buy_events": total_events,
        "amount": total_amount,
        "categories": records(cat, "category_id"),
        "brands": records(brand, "brand"),
        "items": records(item, "item_id"),
        "note": "购买事件聚合；购买用户数独立去重；数量相同按 ID 稳定排序",
    }


# ---------------------------------------------------------------------------
# 汇总导出
# ---------------------------------------------------------------------------

def build_meta(df: pd.DataFrame, sample_meta: dict | None = None) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": int(len(df)),
        "users": int(df["user_id"].nunique()),
        "items": int(df["item_id"].nunique()),
        "categories": int(df["category_id"].nunique()),
        "brands": int(df["brand"].nunique()),
        "time_range_utc": {
            "min": df["event_time"].min().isoformat(),
            "max": df["event_time"].max().isoformat(),
        },
        "timezone": "Asia/Shanghai",
        "behavior_labels": BEHAVIOR_LABELS_ZH,
        "schema": [
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
        ],
        "caveats": [
            "没有年龄/性别/城市字段，不推断人口属性",
            "没有订单号/数量，金额按购买事件单价汇总，每条按 1 件计，不是订单 GMV",
            "没有收藏（fav）事件，漏斗为 浏览 -> 加购 -> 购买",
            "留存为样本首现留存，不是注册留存",
            "category_code / brand 缺失保持 null，不编造",
        ],
        "sample_meta": sample_meta,
    }


def export_analytics(
    df: pd.DataFrame,
    outdir: str | Path,
    *,
    sample_meta: dict | None = None,
    reference_time: pd.Timestamp | None = None,
    half_life_days: float = 3.0,
) -> dict:
    """一次性计算并导出全部统计 JSON / Parquet。"""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    overview_result = overview(df)
    funnel_result = conversion_funnel(df)
    cart_result = cart_abandonment(df)
    rfm, rfm_summary = rfm_table(
        df,
        reference_time=reference_time,
    )
    profiles, profiles_summary = profiles_table(
        df,
        rfm=rfm,
        reference_time=reference_time,
        half_life_days=half_life_days,
    )
    retention_result = retention(df)
    rankings_result = rankings(df)

    write_json(overview_result, out / "overview.json")
    write_json(
        {"daily": overview_result["daily"], "timezone": "Asia/Shanghai"},
        out / "daily.json",
    )
    write_json(
        {"hourly": overview_result["hourly"], "timezone": "Asia/Shanghai"},
        out / "hourly.json",
    )
    write_json(funnel_result, out / "funnel.json")
    write_json(cart_result, out / "cart_abandonment.json")
    write_json(rfm_summary, out / "rfm_summary.json")
    write_json(profiles_summary, out / "profiles_summary.json")
    write_json(retention_result, out / "retention.json")
    write_json(rankings_result, out / "rankings.json")
    write_json(build_meta(df, sample_meta), out / "meta.json")

    rfm.to_parquet(out / "rfm.parquet", index=False)
    profiles.to_parquet(out / "profiles.parquet", index=False)

    # 给前端/答辩用的画像样例
    sample_user_ids = profiles.head(20)["user_id"].tolist()
    samples = []
    for uid in sample_user_ids:
        item = user_profile(
            df,
            uid,
            rfm=rfm,
            reference_time=reference_time,
            half_life_days=half_life_days,
        )
        if item:
            samples.append(item)
    write_json({"items": samples}, out / "profiles_sample.json")

    return {
        "overview": overview_result,
        "funnel": funnel_result,
        "cart_abandonment": cart_result,
        "rfm_summary": rfm_summary,
        "profiles_summary": profiles_summary,
        "retention": retention_result,
        "rankings": rankings_result,
        "meta": build_meta(df, sample_meta),
    }
