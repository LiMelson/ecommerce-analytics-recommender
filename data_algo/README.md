# data_algo · 电商用户行为数据与算法包

这是「电商平台用户行为分析与精准推荐系统」的**数据处理与分析模块**，
由何彦彬负责，与张凯博的推荐/评估模块对接。

它不依赖前端，也不依赖组长后续搭建的 FastAPI 服务：
输入是一份原始 CSV，输出是干净的行为数据、质量报告和统计 JSON。

---

## 一、数据来源

| 项目 | 内容 |
| --- | --- |
| 数据集 | 天池 220316 · eCommerce behavior data from multi category store |
| 原始文件 | `2019-Oct.csv`（5.3GB）、`2019-Nov.csv`（8.4GB） |
| 覆盖时间 | 2019-10 至 2019-11（北京时间按 UTC+8 换算） |
| 字段 | event_time、event_type、product_id、category_id、category_code、brand、price、user_id、user_session |
| 行为类型 | view（浏览）、cart（加购）、purchase（购买）、remove_from_cart（移出购物车） |
| 许可 | 天池页面标注 CC BY-ND 4.0；仅课程内使用，**不公开再分发** |

原始数据放在仓库约定的 `data/raw/` 下（本机独立开发时也可以放在项目根目录的 `archive/`），
体积 13.7GB，**不要提交到 GitHub**。
仓库里只保留代码、测试夹具（几百行合成数据）和 `reports/` 里的小型结果快照。

### 本次实际跑通结果（2019-10 + 2019-11）

| 指标 | 数值 |
| --- | --- |
| 扫描总行数 | 109,950,743 |
| 有效行 / 无效行 | 109,950,743 / 0 |
| 去重后用户数 | 5,316,649 |
| 商品数 | 206,876 |
| 类目数 / 品牌数 | 691 / 4,303 |
| 行为分布 | 浏览 104,335,509 · 加购 3,955,446 · 购买 1,659,788 |
| 1% 用户抽样 | 1,079,354 行 / 52,687 用户 |
| 全量扫描 + 抽样耗时 | 约 3.5 分钟（DuckDB，4 线程） |

### 为什么不用淘宝 UserBehavior 649？

- 649 只有 5 列（用户、商品、类目、行为、时间），没有价格、品牌、类目名；
- 只有 9 天，留存分析很勉强；
- 220316 有真实点击流 + 价格 + 品牌 + 类目名 + 5 个月 + 会话 ID，
  能支撑现有全部页面，还能补上金额、品牌和会话分析。

### 这份数据不能做什么

- **没有年龄/性别/城市**：不推断人口属性，改用 RFM、品牌偏好、价格带等行为画像；
- **没有订单号/数量**：金额按“购买事件单价 × 1 件”汇总，是**行为口径成交额**，不是订单 GMV；
- **没有收藏（fav）**：漏斗改为 浏览 → 加购 → 购买；
- **10-11 月也没有 remove_from_cart**：购物车放弃按“有加购、无购买”的会话/用户商品对判断；
- **没有商品名称**：只展示品牌 + 类目 code + 商品 ID，不编造商品名。

这些取舍会写进数据字典和答辩报告，属于“如实说明数据边界”，不是缺陷。

---

## 二、目录结构

```
data_algo/
├── data_algo/                 # Python 包
│   ├── schema.py              # 数据字典、行为映射、权重、类目中文映射
│   ├── cleaning.py            # 清洗、质量扫描、按用户抽样、加载
│   ├── duckdb_pipeline.py     # DuckDB 大文件快速扫描与抽样
│   ├── analytics.py           # 概览/趋势/漏斗/RFM/画像/留存/排行
│   ├── io_utils.py            # JSON 输出与类型转换
│   └── cli.py                 # 命令行入口
├── tests/
│   ├── fixtures/sample_events.csv   # 合成测试数据（可提交）
│   ├── test_cleaning.py
│   └── test_analytics.py
├── data/processed/            # 抽样 Parquet（不提交）
├── outputs/                   # 质量报告与统计 JSON（不提交）
├── requirements.txt
├── pytest.ini
└── .gitignore
```

---

## 三、快速开始

在 `data_algo/` 目录下执行：

```bash
# 1. 创建虚拟环境（只需一次）
python -m venv .venv

# 2. 安装依赖（国内建议用清华镜像）
.venv/Scripts/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 3. 运行测试
.venv/Scripts/python -m pytest

# 4. 全量扫描 + 按 1% 用户抽样（DuckDB，约 3-4 分钟）
.venv/Scripts/python -m data_algo.cli build-sample --engine duckdb --per-mille 10

# 5. 计算全部统计并导出 JSON
.venv/Scripts/python -m data_algo.cli analytics

# 6. 查看某个用户的画像
.venv/Scripts/python -m data_algo.cli profile --user-id 123456

# 7. 查看抽样数据概况
.venv/Scripts/python -m data_algo.cli info
```

默认会按顺序查找：
1. 团队仓库约定的 `../data/raw/2019-Oct.csv`、`../data/raw/2019-Nov.csv`；
2. 本机独立开发时的 `../archive/2019-Oct.csv`、`../archive/2019-Nov.csv`。

如果文件在别处，用 `--inputs` 指定：

```bash
.venv/Scripts/python -m data_algo.cli build-sample \
  --inputs /path/to/2019-Oct.csv /path/to/2019-Nov.csv
```

---

## 四、抽样规则

- 以**用户**为单位抽样，不是按行截取；
- 同一个 user_id 用 `user_id % 1000 < per_mille` 稳定分组，`--per-mille 10` 约为 1% 用户；
- 被抽中的用户保留其在 10-11 月的**完整行为历史**，避免“历史碎片化”；
- 随机种子固定（默认 `20261116`），结果可复现；
- 非数字 user_id 会用带种子的 blake2b 散列，保证同样可复现；
- 抽样结果写入团队仓库约定的 `data/processed/events_sample.parquet`（在 `data_algo/` 内运行时默认 `../data/processed/`），元信息写入 `outputs/sample_meta.json`。

为什么不全量训练？
- 13.7GB 全量在普通笔记本上做 ItemCF/BPR 很慢，且课程项目不需要；
- 1% 用户通常能得到几十万到上百万条行为，足够跑通基线和对比实验；
- 需要更大规模时，把 `--per-mille` 调大即可（例如 20 = 2%）。

---

## 五、统计口径（重点）

### 时间

- 原始时间统一按 UTC 保存；
- 前端/统计的“每日、每时”统一按北京时间（UTC+8）分组；
- 日期字符串格式 `YYYY-MM-DD`。

### 行为映射

| 原始 event_type | 统一 behavior_type | 说明 |
| --- | --- | --- |
| view | pv | 浏览 |
| cart | cart | 加购 |
| purchase | buy | 购买 |
| remove_from_cart | remove_from_cart | 移出购物车，单独统计 |
| 其他/未知 | 无效 | 写入质量报告的无效原因 |

### 概览

- 活跃用户 = `COUNT(DISTINCT user_id)`；
- 浏览量/加购量/购买量 = 事件条数，不是人数；
- 购买金额 = 购买事件 price 之和，每条按 1 件计，**不是订单 GMV**。

### 顺序漏斗

- 以“同一 user_id + 同一 item_id”为单位；
- 按 event_time 排序，要求 **浏览 → 加购 → 购买** 依次发生；
- 先购买后加购不算完成；收藏不是必经阶段；
- 每个用户商品对在每个阶段最多计一次；
- 与首页“行为总量漏斗”口径不同，页面上会说明。

### RFM

- R = 距参考时刻最近一次购买的天数；没有购买为 `null`，不设为 0；
- F = 购买事件数；没有购买为 0；
- M = 购买事件单价汇总；没有购买或没有有效价格为 `null`；
- 默认阈值：R ≤ 7 天、F ≥ 3 次（针对这份 2 个月数据，可在代码中调整）；
- 分群：潜在转化用户 / 近期购买用户 / 高频活跃用户 / 低频待唤回用户 / 高频待唤回用户。

> 需求文档里 R ≤ 2 天、F ≥ 12 次是针对 9 天演示样本的初始规则；
> 换成 2 个月数据后继续用会几乎把所有人判成“低频”，所以我们把阈值做成可配置，并给出更合理的默认值。

### 用户画像

- 兴趣强度 = 行为权重 × 时间衰减（半衰期默认 3 天），再按类目/品牌汇总；
- 行为权重：浏览 1、加购 5、购买 8（本数据没有收藏）；
- 展示 Top 类目、Top 品牌、价格带、最近行为；
- 不推断年龄、性别、收入等数据未提供的属性。

### 留存

- cohort = 用户在样本中第一次出现的日期；
- 第 d 天留存率 = 该 cohort 在第 d 天活跃的去重用户数 ÷ cohort 用户数；
- 观察窗口尚未覆盖的日期返回 `null`，前端显示“—”；
- 已覆盖但无人回访返回 `0`；
- 这是“样本首现留存”，不是注册留存。

### 购买排行

- 先过滤 buy，再按类目/品牌/商品聚合；
- 购买用户数独立去重；
- 数量相同按 ID 升序稳定排序；
- 没有购买事件时返回空排行，不用浏览量填充。

### 购物车放弃（这份数据的加分项）

- 会话层面：有加购、没有购买的会话算放弃；
- 用户商品层面同理；
- 目前按“是否存在事件”统计，不严格按时间顺序归因。

---

## 六、输出文件

运行 `build-sample` 后：

```
data/processed/events_sample.parquet   # 抽样行为数据
outputs/quality_report.json            # 机器可读质量报告
outputs/quality_report.md              # 可贴进报告的 Markdown
outputs/sample_meta.json               # 抽样参数与统计
```

运行 `analytics` 后：

```
../data/processed/events_sample.parquet  # 抽样行为数据（团队仓库约定）
outputs/analytics/overview.json          # 概览 + 每日 + 每时
outputs/analytics/daily.json
outputs/analytics/hourly.json
outputs/analytics/funnel.json
outputs/analytics/cart_abandonment.json
outputs/analytics/rfm_summary.json
outputs/analytics/rfm.parquet          # 每个用户的 R/F/M
outputs/analytics/profiles_summary.json
outputs/analytics/profiles.parquet     # 每个用户的画像标量字段
outputs/analytics/profiles_sample.json # 20 个完整画像样例
outputs/analytics/retention.json
outputs/analytics/rankings.json
outputs/analytics/meta.json            # 数据版本、口径、限制
```

---

## 七、和前端 / 后端对接

当前前端 [behavior.js](../frontend/src/data/behavior.js) 里的统计函数是演示口径，
后续把数据源换成我们的 JSON 即可。建议接口：

| 页面 | 建议接口 | 对应输出 |
| --- | --- | --- |
| 首页 / 数据大屏 | `GET /api/v1/overview` | `overview.json` |
| 每日趋势 | `GET /api/v1/analytics/daily` | `daily.json` |
| 每时趋势 | `GET /api/v1/analytics/hourly` | `hourly.json` |
| 漏斗 | `GET /api/v1/analytics/funnel` | `funnel.json` |
| RFM | `GET /api/v1/analytics/rfm` | `rfm_summary.json` + `rfm.parquet` |
| 用户画像 | `GET /api/v1/users/{id}/profile` | `user_profile()` 的返回值 |
| 留存 | `GET /api/v1/analytics/retention` | `retention.json` |
| 排行 | `GET /api/v1/analytics/rankings` | `rankings.json` |
| 智能推荐 | 后续由张凯博模块提供 | — |

前端需要逐步适配的字段：
- `category_code` / `brand` 可选展示，替换现在的“类目 982926”；
- `remove_from_cart` 单独展示“购物车放弃率”；
- 推荐卡片可以显示“品牌 + 类目 + 价格”，不再只有匿名 ID；
- RFM 页面可以把 M 从“缺失”改为“行为口径消费额”，并注明不是订单 GMV。

---

## 八、下一步

1. **推荐算法**（与张凯博一起）：
   - 热门兜底（训练窗口内行为热度）；
   - ItemCF（用户-商品共现/余弦相似度，TopN 邻居）；
   - BPR（隐式反馈成对排序，负采样 + 固定随机种子）；
2. **统一评测**：
   - 全局时间切分（约 70% / 15% / 15%）；
   - Recall@10、NDCG@10、Precision@10、HR@10、覆盖率；
   - 冷启动分组（低频/高频用户）和推理耗时；
3. **消融实验**：
   - 行为权重（浏览/加购/购买）；
   - 时间衰减（开/关、不同半衰期）；
   - 会话特征（长期 + 短期兴趣）。

---

## 九、测试

```bash
.venv/Scripts/python -m pytest
```

当前覆盖：
- CSV 解析、行为映射、UTC 时间、北京时间派生；
- 无效行、重复行、缺失 category/brand、负价格；
- 按用户抽样稳定性与完整历史；
- 概览、每日/每时、顺序漏斗、RFM 缺失 R、留存 null/0、排行、购物车放弃、用户画像。

---

## 十、团队合并说明

1. 本目录已经按团队仓库的约定放在根目录 `data_algo/`；
2. 不要提交 `data/raw/`、`data/processed/`、`outputs/`、`.venv/`；
3. 组长/队友拉代码后执行一次 `pip install -r requirements.txt`；
4. 需要重新生成数据时，把原始 CSV 放到 `data/raw/`（或本地 `archive/`），运行 `build-sample`；
5. 如果后端用 FastAPI，直接 `from data_algo.analytics import overview, conversion_funnel, ...`，
   或读取 `data_algo/reports/` 里的 JSON 快照；
6. 推荐/评测模块可以直接 import `load_events`，拿到干净的 DataFrame；
7. 真实的 20MB 抽样 Parquet 不提交；需要时按 `build-sample` 重新生成，或向数据负责人索取。
