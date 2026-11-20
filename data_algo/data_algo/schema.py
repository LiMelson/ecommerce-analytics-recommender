"""数据字典与常量定义。

原始数据来自 Tianchi 220316（eCommerce behavior data from multi category store），
字段为：
    event_time, event_type, product_id, category_id, category_code,
    brand, price, user_id, user_session

我们把它统一成下面这套“规范字段”，方便后续统计、推荐和对接后端。
所有 ID 一律按字符串处理，避免大整数在前端丢精度。
"""

from __future__ import annotations

UTC = "UTC"
CN_TZ = "Asia/Shanghai"

# 原始文件的必需列（没有这些列就无法继续）
RAW_REQUIRED_COLUMNS = [
    "event_time",
    "event_type",
    "product_id",
    "user_id",
]

# 原始文件的完整列（缺列时会补空，不阻断导入）
RAW_OPTIONAL_COLUMNS = [
    "category_id",
    "category_code",
    "brand",
    "price",
    "user_session",
]

# 统一后的字段名
CANONICAL_COLUMNS = [
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

# 原始行为 -> 统一行为
# view 映射为 pv，是为了兼容前端现有的“浏览”口径；
# remove_from_cart 是这份数据集额外提供的信号，单独保留，不混进四类核心行为。
BEHAVIOR_MAP = {
    "view": "pv",
    "pv": "pv",
    "click": "pv",
    "cart": "cart",
    "addtocart": "cart",
    "add_to_cart": "cart",
    "purchase": "buy",
    "buy": "buy",
    "order": "buy",
    "fav": "fav",
    "favorite": "fav",
    "collect": "fav",
    "remove_from_cart": "remove_from_cart",
    "remove": "remove_from_cart",
}

# 前端现有的四类核心行为
CORE_BEHAVIORS = ("pv", "fav", "cart", "buy")

# 辅助行为：参与质量报告和购物车放弃分析，但不进入核心漏斗
AUX_BEHAVIORS = ("remove_from_cart",)

BEHAVIOR_LABELS_ZH = {
    "pv": "浏览",
    "fav": "收藏",
    "cart": "加购",
    "buy": "购买",
    "remove_from_cart": "移出购物车",
}

# 兴趣强度权重。文档初始建议 pv=1 / fav=3 / cart=5 / buy=8；
# 本数据集没有 fav，因此实际只会用到 pv/cart/buy。
INTEREST_WEIGHTS = {
    "pv": 1.0,
    "fav": 3.0,
    "cart": 5.0,
    "buy": 8.0,
}

# RFM 的默认阈值。需求文档里 2 天 / 12 次是针对 9 天演示样本的；
# 这份数据横跨 2 个月，阈值需要放宽，且全部可配置。
DEFAULT_RECENT_DAYS = 7
DEFAULT_HIGH_FREQUENCY = 3

# 价格带（只用于画像展示，不做金额归因）
PRICE_BINS = [0, 50, 100, 300, 1000, float("inf")]
PRICE_BIN_LABELS = ["0-50", "50-100", "100-300", "300-1000", "1000+"]

# 顶层类目中文映射：只映射一份数据里能确认的顶层前缀，
# 原始 category_code 会同时保留，绝不编造商品名。
CATEGORY_TOP_ZH = {
    "appliances": "家电",
    "apparel": "服饰",
    "accessories": "配饰",
    "auto": "汽车用品",
    "beauty": "美妆个护",
    "books": "图书",
    "computers": "电脑办公",
    "construction": "家装建材",
    "electronics": "数码电子",
    "furniture": "家具家居",
    "kids": "母婴儿童",
    "medicine": "医药健康",
    "sport": "运动户外",
    "stationery": "文具",
    "toys": "玩具",
    "unknown": "未分类",
}


def _is_missing(value) -> bool:
    """不依赖 pandas 的缺失值判断，兼容 None / NaN / pd.NA / pd.NaT。"""
    if value is None:
        return True
    # pandas 的 NA / NaT 不能直接 bool，先按类型名判断
    if type(value).__name__ in {"NAType", "NaTType"}:
        return True
    try:
        if value != value:  # NaN
            return True
    except Exception:
        pass
    try:
        text = str(value).strip().lower()
    except Exception:
        return False
    return text in {"", "nan", "nat", "none", "<na>"}


def top_category(code: str | None) -> str:
    """取 category_code 的顶层前缀，例如 electronics.smartphone -> electronics。"""
    if _is_missing(code):
        return "unknown"
    text = str(code).strip()
    if not text or text.lower() == "nan":
        return "unknown"
    return text.split(".", 1)[0].strip().lower() or "unknown"


def category_label(code: str | None) -> str:
    """类目展示文案：中文顶层 + 原始 code，避免编造。"""
    if _is_missing(code):
        return "未分类"
    top = top_category(code)
    if top == "unknown":
        return "未分类"
    zh = CATEGORY_TOP_ZH.get(top, top)
    return f"{zh} / {code}"
