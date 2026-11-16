# 数据字典（v0.1）

本文档对应数据集：天池 220316 · eCommerce behavior data from multi category store。
原始文件：`2019-Oct.csv`、`2019-Nov.csv`。

---

## 1. 原始字段

| 原始字段 | 类型 | 必填 | 含义 | 处理规则 |
| --- | --- | --- | --- | --- |
| event_time | 字符串（UTC） | 是 | 行为发生时间，格式 `2019-10-01 00:00:00 UTC` | 解析为 UTC 时间；无法解析的进无效报告 |
| event_type | 枚举 | 是 | view / cart / purchase / remove_from_cart | 映射为统一行为，见第 2 节 |
| product_id | 字符串/大整数 | 是 | 商品 ID | 统一为字符串，避免前端精度丢失 |
| category_id | 字符串/大整数 | 否 | 类目 ID | 缺失记为 `unknown` |
| category_code | 字符串 | 否 | 类目层级，如 `electronics.smartphone` | 保留原始值；缺失保持 null |
| brand | 字符串 | 否 | 品牌名，如 `xiaomi` | 保留原始值；缺失保持 null |
| price | 浮点数 | 否 | 事件对应的商品单价 | 转 float；空值保留；负价格进无效报告；0 价单独计数 |
| user_id | 字符串/大整数 | 是 | 匿名用户 ID | 统一为字符串；缺失进无效报告 |
| user_session | 字符串 | 否 | 会话 ID | 统一为字符串；缺失保持 null |

---

## 2. 行为映射

| 原始值 | 统一值 | 是否核心行为 | 说明 |
| --- | --- | --- | --- |
| view | pv | 是 | 浏览 |
| cart | cart | 是 | 加购 |
| purchase | buy | 是 | 购买 |
| remove_from_cart | remove_from_cart | 否（辅助） | 移出购物车，用于放弃分析 |
| 其他/未知 | — | — | 无效，写入质量报告 |

> 本数据集没有收藏（fav），因此前端如果保留“收藏”列，应显示 0 或“该数据集无此行为”。

---

## 3. 规范字段（清洗后）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| user_id | string | 匿名用户 |
| item_id | string | 商品（由 product_id 重命名） |
| category_id | string | 类目 ID |
| category_code | string / null | 类目层级 |
| brand | string / null | 品牌 |
| behavior_type | string | pv / cart / buy / remove_from_cart |
| raw_event_type | string | 原始行为，便于追溯 |
| event_time | timestamp[UTC] | 发生时间 |
| price | float / null | 事件单价 |
| session_id | string / null | 会话（由 user_session 重命名） |

派生字段（加载时生成，不写入 Parquet）：

| 字段 | 说明 |
| --- | --- |
| event_time_cn | 北京时间（UTC+8） |
| date | 北京时间的日期 `YYYY-MM-DD` |
| hour | 北京时间的小时 0-23 |
| event_ts | UTC 时间的 int64 纳秒，用于稳定排序 |

---

## 4. 指标口径

| 指标 | 计算 | 注意事项 |
| --- | --- | --- |
| 活跃用户 | `COUNT(DISTINCT user_id)` | 不能把每日去重人数相加 |
| 浏览量 / 加购量 / 购买量 | 对应行为的事件条数 | 不是人数 |
| 购买金额 | Σ purchase.price | 每条购买事件按 1 件计；不是订单 GMV |
| 漏斗-浏览 | 有 pv 的用户商品对数 | 同用户同商品 |
| 漏斗-加购 | 先 pv 后 cart 的用户商品对数 | 先买后加购不算 |
| 漏斗-购买 | 先 pv、再 cart、再 buy 的用户商品对数 | 收藏不是必经 |
| R | 距参考时刻最近一次购买的天数 | 无购买为 null |
| F | 购买事件数 | 无购买为 0 |
| M | Σ purchase.price | 无购买/无价格为 null |
| 兴趣强度 | Σ 行为权重 × 0.5^(距今天数/半衰期) | 按类目/品牌汇总 |
| 留存 | cohort 第 d 天活跃用户 / cohort 人数 | 窗口未覆盖为 null，已覆盖未回访为 0 |
| 购物车放弃 | 有 cart 且无 buy 的会话 / 有 cart 的会话 | 会话层面，不严格按顺序 |
| 商品覆盖率 | 推荐列表去重商品数 / 评估候选商品数 | 推荐模块后续补充 |

---

## 5. 清洗规则

1. **必需字段缺失** → 无效行，记录行号与原因；
2. **时间无法解析** → 无效行；
3. **行为类型未知** → 无效行；
4. **价格为负** → 无效行；
5. **价格为空** → 保留事件，金额统计时忽略；
6. **价格为 0** → 保留，单独计数（可能是赠品/活动）；
7. **category_id 缺失** → 记为 `unknown`；
8. **category_code / brand 缺失** → 保持 null，不编造；
9. **完全重复行** → 按完整字段组合去重（块内 + 加载时再查一次）；
10. **ID 一律字符串** → 避免前端浮点精度和前导零问题。

---

## 6. 已知限制

- 没有年龄、性别、城市、注册日期 → 不做人口属性推断，留存叫“样本首现留存”；
- 没有订单号、数量、优惠、运费 → 金额是行为口径，不是订单 GMV；
- 没有收藏行为 → 漏斗是 浏览 → 加购 → 购买；
- 没有商品名称 → 只展示品牌 + 类目 code + 商品 ID；
- 类目/品牌是英文 → 只对顶层类目做中文映射，保留原始 code；
- 会话 ID 由平台提供，跨设备/跨浏览器可能不一致；
- 数据只覆盖 2019-10 至 2019-11，不能代表全年或全平台。
