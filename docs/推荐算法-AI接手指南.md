# 推荐算法 AI 接手指南

算法负责人张凯博完成推荐算法、模型训练与评估、保存和加载、Python 与 HTTP 推理、算法测试与研究和算法工程交付。团队其余成员负责正式数据与模型一致性核验、`dataset_id` 绑定、业务后端接入、前端展示、部署权限与网络、并发及网站端到端验收。独立算法服务已验收，正式网站集成待接手组员完成。 [同学快速接手说明](推荐算法-接手说明.md)

## 仓库与入口

| 路径 | 负责范围及入口 |
| --- | --- |
| `data_algo/` | 数据组清洗、确定性抽样、质量报告及五列网站导出 |
| `backend/app/` | 业务组数据导入、版本、鉴权与算法 HTTP 转发 |
| `frontend/`、`infra/` | 页面与部署配套，由前端及系统集成人员负责 |
| `recommendation/sample.py` | `sha256_file(path)`、`read_sample(path, config)` 验证字节指纹和 Arrow 物理类型，返回 UTC 事件 DataFrame、存储元数据、文件哈希 |
| `recommendation/benchmark.py` | `time_boundaries`、`split_events`、`BenchmarkStage.from_split`、`History.fit`、`HistoryBands.fit`、`Labels.for_user`、`evaluate` 和 `ranking_metrics` |
| `recommendation/training_snapshot.py` | `training_snapshot_sha256(events)` 对规范化表内容生成与行序无关的训练指纹 |
| `recommendation/popular.py`、`itemcf.py`、`bpr.py` | `PopularModel`、`ItemCFModel`、`BPRModel`。fit 从历史生成不可变模型，recommend 返回来源和有序候选 |
| `recommendation/assets.py`、`bpr_io.py` | `save_asset`、`load_asset` 处理统一资产，BPR 内层由 `save_model`、`load_model` 处理 |
| `recommendation/engine.py`、`delivery.py` | `RecommendationEngine` 为 Python 统一入口。CLI 提供 `build`、`inspect`、`load`、`predict`、`batch` |
| `recommendation/http_service.py`、`http_check.py` | `create_app` 和环回 CLI 提供独立服务。回归工具覆盖实际 TCP 与原模型排序 |
| `recommendation/itemcf_research.py`、`weighted_itemcf.py`、`recommendation/research07/` | P0-REC-05、07 的独立研究入口、配置与结果 |
| `recommendation/tests/`、`recommendation/reports/` | 单元、资产、接口及研究测试和冻结聚合报告 |

## 数据与评估边界

来源为 REES46/Open CDP 多类目电商点击流，项目数据版本采用天池 220316 的 2019 年 10、11 月记录。原字段包括 event_time、event_type、product_id、category_id、category_code、brand、price、user_id、user_session。清洗后 Parquet 字段为 `event_time, behavior_type, raw_event_type, item_id, category_id, category_code, brand, price, user_id, session_id`。保留原 ID 字符串，`view → pv`、`purchase → buy`，会话和类目按数据组规则处理。按用户抽样，样本内数值用户均满足 `user_id % 1000 < 10`，有效事件整行去重。样本 1,079,354 行，52,687 用户，81,693 商品。Arrow 时间为无时区 `timestamp[us]`，依上游约定附加 UTC。 [数据选型](数据选型与处理说明.md) · [样本验收](P0-REC-01-数据验收.md)

Parquet SHA-256 为 `7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771`。`local-parquet-sha256-` 加该摘要是现有发布资产的 dataset_id。历史从同一 Parquet 再生的网站五列 CSV 摘要为 `ec64ad5f5c7157d2425710232f550872044d871f88050b211c46ad97869513bc`。该摘要是历史对照值，实际导入批次需复核。

| 阶段 | UTC 起止，左闭右开 | 拟合范围 |
| --- | --- | --- |
| 训练 | 2019-10-01 00:00 至 2019-11-12 16:48 | 初始训练 |
| 验证 | 2019-11-12 16:48 至 2019-11-21 20:24 | 仅初始训练 |
| 测试 | 2019-11-21 20:24 至 2019-12-01 00:00 | 冻结方案以训练加验证拟合 |

协议 `purchase_global_utc_v1`。已购过滤对每个用户使用截止点前的 buy 集合。候选目录使用截止前曾出现的商品。窗口内购买按商品去重，去掉未来首现和历史已购后得到可达目标。主指标只对有可达购买目标的人等权宏平均。验证主分母 2,069，测试主分母 1,122。Recall 分母为目标数，Precision 固定除以 K=10，HR 为是否任意命中，NDCG 使用二值相关性、log₂ 折扣和理想 DCG。覆盖率分母为拟合目录大小。评估器同时保留含不可达目标的附加口径与全窗口覆盖率。冷启动为拟合期零事件，验证和正式测试的低/中/高档为 1–2、3–15、≥16 条，由初始训练期 nearest-rank P25/P75 冻结。 [Benchmark](P0-REC-01-Benchmark.md)

## 正式模型

- **Popular**。`PopularModel.fit` 在历史目录上计算 buy 事件次数，降序按次数、同分按字符串商品 ID 升序。零购买商品仍可进入候选。类目取最近已知值，同一时刻按类目 ID 稳定选择。`recommend(user_id, k)` 跳过该用户的历史已购。
- **ItemCF**。`binary_interactions` 将 `CORE_BEHAVIORS`（pv、fav、cart、buy）映射为二值交互，对每用户保留最近 100 件不同商品。`cosine_neighbors` 以 256 行稀疏块计算商品共现余弦，每件最多保留 80 个邻居。推理对训练历史种子汇总候选相似度，过滤已购，按分数和 ID 稳定排序，从同快照 Popular 补位。冻结方案 `n80_recent100`。
- **BPR-MF**。`BPRModel.fit` 按去重用户商品购买对构造正例，`sample_negatives` 从历史目录减去该用户购买集合中均匀抽负例。`pairwise_loss` 优化正负内积差的成对排序损失与正则。冻结配置为 16 维、40 epoch、学习率 0.05、正则 0.01、batch 128、每正例 3 个负例、seed 20261120。推理仅使用有训练正信号的商品，过滤已购，无购买因子或有效候选不足时使用同快照 Popular。

三模型给无历史用户提供热门冷启动。ItemCF 可使用仅浏览历史，购买版 BPR 对这类用户使用热门。独立引擎始终保留模型输出的排序，`predict_with_trace` 额外给出 `source, item_sources, fallback_reason`。关键条目来源包括 `popular_purchase_count`、`cold_start_popular_purchase_count`、`itemcf_cosine`、`popular_fill`、`popular_no_neighbors`、`bpr_mf`、`popular_bpr_fill`、`popular_no_bpr_positive`、`popular_no_trainable_vector`、`popular_invalid_bpr_score`。对外 `reason` 是相应中文说明。热度、余弦和内积是各自的排序信号，混合列表始终按 `rank` 展示。 [最终技术报告](P0-REC-06-推荐算法最终技术报告.md)

## 模型资产与版本

固定路径为 `artifacts/p0-rec-04a/release-assets/`。从授权的课程组渠道取得原四文件 ZIP，直接解压到该目录。若部署目录不同，`--registry` 或 `from_registry` 接受明确的注册表路径。四个文件的可信 SHA-256 如下。

| 文件 | SHA-256 |
| --- | --- |
| `registry.json` | `ab60e041a5c93b68e4f569cd11c714bf4ab99ca54278cbc0c188ad69e60d17e0` |
| `popular_v1.asset` | `9b5877926de569427d9a79b0a8adac1242d63ea93cbad8accb5224e8fc1abd5f` |
| `itemcf_v1.asset` | `d0cd836bb123ab0d67a9945b2160a201ac7713dbcf93e01ed6239ca79b91f739` |
| `bpr_mf_buy_v1.asset` | `27dcd8d1bdda54387d2bd9ee303a8aade1022a3afb4bb076b7843572810b1155` |

归档检查未发现凭证模式。三个资产都带有训练用户 ID 和已购映射，ItemCF 还保存交互矩阵及用户映射，BPR 还保存用户因子及映射。项目来源记录注明天池 CC BY-ND 4.0 和课程范围；Kaggle 发布者允许署名后用于研究、书籍及教学，元数据仍把数据版权归原作者，公开条款未明确授权这种含用户级数据的再发布。本项目对四个原文件使用受控课程组交付，`artifacts/` 由 Git 忽略。 [数据来源记录](数据选型与处理说明.md) · [发布者 Kaggle 数据页](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store) · [Kaggle 许可元数据](https://www.kaggle.com/api/v1/datasets/view/mkechinov/ecommerce-behavior-data-from-multi-category-store)

`recommendation-registry-v1` 的 `models` 按模型版本索引 `file, kind, sha256, dataset_id, code_sha256`。相对 `file` 必须解析在注册表目录内。引擎核验完整文件哈希，再校验 JSON 与数值数组成员哈希、类型、ID/维度、版本、数据和构建代码身份。三个正式模型共享 2019-10-01 00:00 到 2019-11-21 20:24 UTC 的训练快照，内容指纹 `95a6fc7a551b2385beac77fa09783ed45d2de9dd7b003e7a725db0f6e6ab1f02`。同一 dataset_id 加载多个模型时，引擎核验快照、协议和拟合边界一致。个性化模型内含同快照 Popular。加载用 `allow_pickle=False`，保存使用临时文件、试加载与原子替换。 [资产协议](P0-REC-04A-算法模块交付.md)

正式网站实际入库批次须核对哈希、时间及行为映射、用户/商品/类目 ID、候选与快照。由集成组用可追溯方式发版兼容资产和注册表，并按业务端的 dataset_id 和 model_version 登记。只改注册表字符串会触发资产身份冲突。 [数据与算法约定](数据与算法交付约定.md)

## Python 与 HTTP

~~~python
from recommendation.engine import RecommendationEngine

engine = RecommendationEngine.from_registry("artifacts/p0-rec-04a/release-assets/registry.json")
version = "itemcf_v1"
dataset_id = engine.describe(version)["model"]["dataset_id"]
options = {"dataset_id": dataset_id, "model_version": version, "limit": 3}
response = engine.predict(user_id="course-demo-user", **options)
batch = engine.predict_batch(["course-demo-user", "course-demo-user"], **options)
~~~

默认预加载。按需加载用 `from_registry(path, preload=False)` 再 `load(version)`，接受请求后复用引擎。Python 批量保留输入顺序和重复，遇无效请求抛 `AssetError`，查看 `code`。CLI 的 inspect/load 接收 `--registry`、`--model-version`，predict 还接收 `--user-id`、`--dataset-id`、`--limit`。CLI batch 接收 `--users-json`，输入最多 10,000 个 ID，仅输出汇总。

| 路由 | 契约 |
| --- | --- |
| `GET /health`、`GET /ready` | 已预载时返回 200，JSON 含 `status: "ready", model_versions, model_load_seconds`；应用未就绪路径返回 503，启动加载失败会导致进程退出 |
| `POST /predict` | JSON 四字段均必填。字符串 `user_id` 1–128 字符且无首尾空白和低位控制字符；`dataset_id` 1–128 字符且精确匹配；`model_version` 1–64 字符且已登记；`limit` 必须为整数 1–10。严禁额外字段 |
| 成功 | 回显三个标识和 `items`；每项 `item_id, category_id, rank, score, reason`。rank 从 1 连续、商品唯一。未知类目为空，当前引擎产出有限数值分数。200 空列表附头 `X-Recommendation-Status`（`empty_candidate_catalog` 或 `no_eligible_items`）。响应上限 1 MiB |

可用 [实际 PowerShell 请求](推荐算法-接手说明.md#手动跑起来) 用注册表生成 payload。下面显示请求 JSON 结构与同一个请求的成功响应格式，例中商品字段为演示值。

~~~json
{
  "user_id": "course-demo-user",
  "dataset_id": "local-parquet-sha256-7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771",
  "model_version": "itemcf_v1",
  "limit": 1
}
~~~

~~~json
{
  "user_id": "course-demo-user",
  "dataset_id": "local-parquet-sha256-7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771",
  "model_version": "itemcf_v1",
  "items": [{"item_id":"example-item","category_id":"","rank":1,"score":4.0,"reason":"训练截止前无用户历史，使用购买热门兜底"}]
}
~~~

错误体形如 `{"error":{"code":"dataset_mismatch","message":"Dataset does not match the selected model"}}`。

| HTTP | error.code | 场景 |
| --- | --- | --- |
| 400 | `invalid_json` | JSON 无法解析 |
| 422 | `missing_field`、`invalid_request`、`invalid_user_id`、`invalid_limit` | 缺必填字段、非法字段/类型或取值 |
| 404 | `unknown_model` | 版本未登记 |
| 409 | `dataset_mismatch` | 请求数据版本与选中模型冲突 |
| 503 | `model_not_loaded`、`model_data_missing`、`model_corrupt`、`model_identity_mismatch` | 应用未就绪或模型故障；预加载遇同类资产错误会启动失败 |
| 500 | `model_invalid_output`、`inference_error` | 输出非法或推理出现意外异常 |
| 路由状态 | `http_error` | 框架路由错误 |

`http_service` CLI 只接受 `127.0.0.1` 或 `::1`，默认端口 9001。业务端已有 `backend/app/api.py` 的 `recommendations` 适配器，经 `RECOMMENDER_URL` POST 完整上游 URL（包含 `/predict`），使用活动数据和模型 ID，超时 5 秒、最高 1 MiB，核对回显、排序和去重。集成组负责配置拓扑和服务权限、鉴权以及超时与故障策略，前端按 rank 展示；最后在目标环境做网络、并发、就绪、回滚和网站端到端验收。

## 冻结结果与后续边界

公开冻结测试结果以共同主分母 1,122 计算。Popular 的 Recall/NDCG 为 0.17365/0.10100（16 件），ItemCF 0.14113/0.07908（5,372 件），BPR 0.13775/0.07727（727 件）。样本取模、时间窗、训练候选和已购过滤会影响指标。指标反映离线购买排序，线上转化与推广效果需独立验证。BPR 在完整测试窗口 15,158 位用户中有 13,092 位走热门兜底。

P0-REC-05 在验证集比较原 ItemCF 与行为加权、七天衰减、组合。三种改动均未证明总体收益，保留原版。P0-REC-07 预注册 H0–H3，选优后以用户哈希隔开的确认子集评估 H1。NDCG 增量 +0.001838，95% 配对区间 [+0.000338, +0.003592]，低于 +0.002 门槛。H1 保留为研究结果，正式注册表仍为三个版本。已结束本课程项目的推荐效果优化。 [P0-REC-05 消融](P0-REC-05-推荐算法改进与消融实验.md) · [P0-REC-07 收尾](P0-REC-07-最后一次推荐优化实验.md)

## 接手检查与验收

1. 从仓库根目录执行 `git status --short --branch` 和 `git log --oneline -15`，确认分支和范围。阶段提交主要锚点如下。最终交付分支 `feat/recommendation-final-delivery` 从研究收尾 `f7c8db4` 建立，正式二进制沿用 P0-REC-04A 原发布资产。
2. 按下列命令验证固定 Python 3.12 环境。分别启动两套 pytest，避免 `data_algo` 的两种导入路径在同进程冲突。已有 Starlette TestClient 弃用提示。数据组目录存在既有 Ruff 问题，推荐 lint 与 format 配置以 `backend/pyproject.toml` 为准。
3. 取得受控资产，核验四文件哈希。三个模型必须可实际加载并成功处理 Python 单用户/批量与独立 HTTP 请求。用错误数据版本试验 409；检查 `/ready` 列出三个版本。排序回归使用已冻结的请求用户队列和历史指纹，验收禁止重新读取购买标签评分。
4. 保留冻结边界、模型版本、随机种子、训练和评估口径、排序与版本校验。研究代码和结果保持隔离。正式资产和 Parquet 放在受控目录。新变更以冻结源码和聚合结果为基准。
5. 若接手正式集成，先核对实际入库版本和拟合身份，再配置模型登记与业务后端，之后验收前端展示、目标环境资源、故障状态和端到端行为。 [本轮聚合验收证据](../recommendation/reports/final-delivery-check.json)

| 阶段 | 提交 |
| --- | --- |
| 01 Benchmark 与 Popular | `fab15a9` |
| 02 ItemCF 与快照防护 | `04668a6`、`34a01e6` |
| 03 BPR | `0d25a9f` |
| 04A Python 与资产，04B HTTP | `fdeeb73`、`9b226b3` |
| 05 消融，06 验收 | `38e9e17`、`fbfabe8` |
| 07 预注册、实现、收尾 | `5a013d3`、`032fa2f`、`f7c8db4` |

~~~powershell
py -3.12 -m venv .venv
$py = (Resolve-Path '.venv/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
& $py -m pip install -r recommendation/requirements-http.txt ruff==0.17.5
& $py -m pytest -q recommendation/tests
& $py -m pip check
Push-Location data_algo
try {
    & $py -m pytest -q tests
} finally {
    Pop-Location
}
& $py -m ruff check --config backend/pyproject.toml recommendation
& $py -m ruff format --check --config backend/pyproject.toml recommendation
Get-FileHash -Algorithm SHA256 -LiteralPath (Get-ChildItem -File 'artifacts/p0-rec-04a/release-assets').FullName
& $py -m recommendation.http_service --host 127.0.0.1 --port 9001
~~~

已有正式资产推理无需样本。离线入口和受控样本的复核步骤在历史文档中保留。涉及购买标签评分或冻结模型重建时，以维护者正式授权和研究协议为准。本轮交付阶段只做既有模型的工程验收。 [P0-REC-06 完整运行说明](P0-REC-06-算法模块最终交接.md)

## 复制给接手 AI 的指令

> 读取 docs/推荐算法-AI接手指南.md 与 recommendation/README.md，核对当前 Git 状态、三个正式模型、数据与资产哈希。以原发布资产运行推荐与数据测试、Ruff、Python 和 HTTP 冒烟。接手范围是网站集成，先核验实际导入批次并绑定正式 dataset_id，再接业务后端与前端，按 rank 展示。保留原算法、实验和版本校验。输出实际验证过的结果及仍待确认的事项。
