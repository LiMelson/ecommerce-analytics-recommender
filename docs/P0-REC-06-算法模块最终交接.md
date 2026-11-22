# P0-REC-06 算法模块最终交接

张凯博 · 2026-11-21

本交付支持离线受控资产构建、已有模型独立推理与环回 HTTP。调用方需从受控资产注册表获得 dataset_id，用同一模型版本组合调用接口。正式网站的入库数据与模型绑定由集成人员核对。 [技术报告](P0-REC-06-推荐算法最终技术报告.md) · [团队数据契约](数据与算法交付约定.md)

## 文件和运行环境

| 入口 | 职责 |
| --- | --- |
| `recommendation/sample.py`、`benchmark.py`、`training_snapshot.py` | Parquet 身份、UTC、候选与标签、离线指标及训练快照 |
| `popular.py`、`itemcf.py`、`bpr.py`、`bpr_io.py` | 三个正式模型与 BPR 数值序列化 |
| `assets.py`、`engine.py`、`delivery.py` | 自包含模型资产、统一引擎与命令行 |
| `http_service.py`、`http_check.py` | 本地 HTTP 服务，以及真实 TCP 和冻结指纹复核 |
| `delivery_check.py` | 离线固定资产构建复现、原内存与加载后对照、Python 性能 |
| `itemcf_research.py`、`weighted_itemcf.py`、`itemcf_research_v1.json` | 验证集消融，独立于正式模型注册表 |
| `recommendation/tests/`、`data_algo/tests/` | 合成及小样本测试；数据侧测试另需原样的 `backend/app/importing.py` |
| `recommendation/reports/` | 冻结聚合报告与新版验收证据；旧报告用于资产构建时的参数核对 |

正式版本为 `popular_v1`、`itemcf_v1`（n80_recent100）、`bpr_mf_buy_v1`（buy、16 维、40 Epoch、seed 20261120）。依赖入口为 `recommendation/requirements.txt`（引用 `data_algo/requirements.txt`，含 pandas 3.1.1、NumPy 2.5.5、SciPy 1.18.1、PyArrow 26.0.1、DuckDB 2.0.0、pytest 9.2.0）。HTTP 扩展使用 `recommendation/requirements-http.txt`（FastAPI 0.163.1、Uvicorn 0.54.0、HTTPX 0.28.1 等）。本机重新从固定依赖文件安装到独立 Windows 11 x64 / Python 3.12.6 虚拟环境。依赖来源沿用运行机器配置的包源。

## 1. PowerShell 准备和测试

在仓库根目录或审查包源码根目录执行。VSCode 可选择同一 `.venv/Scripts/python.exe` 作为解释器。

```powershell
py -3.12 -m venv .venv
$py = (Resolve-Path '.venv/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
& $py -m pip install -r recommendation/requirements-http.txt ruff==0.17.5
& $py -m pip check
& $py -m pytest -q recommendation/tests
Push-Location data_algo
try {
    & $py -m pytest -q tests
} finally {
    Pop-Location
}
& $py -m ruff check --config backend/pyproject.toml recommendation
& $py -m ruff format --check --config backend/pyproject.toml recommendation
```

推荐与数据测试应分别启动 pytest。两套目录在同一进程下会使用不同的 `data_algo` 包查找方式，导致收集错误。上述独立命令实测推荐 130 项、数据 33 项通过，推荐 Ruff lint 和 format 通过。数据目录应用相同 Ruff 配置会报告既有问题，数据测试本身通过。推荐单测可用合成数据；以下实际样本和实资产的命令需另行取得对应文件。

## 2. 准备并核验模型资产

从受控渠道取得相互匹配的四个文件，放在同一个本地目录。推荐沿用 `artifacts/p0-rec-04a/release-assets/`，或通过参数传递绝对路径。单独的受控资产交付清单提供注册表、每个文件的 SHA-256 及来源位置。以下同时核验注册表可信摘要和该表登记的三个文件摘要。若交付另一版本，使用该版本由可信渠道发布的清单。

```powershell
$registry = (Resolve-Path 'artifacts/p0-rec-04a/release-assets/registry.json').Path
$expectedRegistry = 'ab60e041a5c93b68e4f569cd11c714bf4ab99ca54278cbc0c188ad69e60d17e0'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $registry).Hash -ne $expectedRegistry) {
    throw 'Unexpected registry SHA-256'
}
$registered = (Get-Content -Raw -Encoding UTF8 -LiteralPath $registry | ConvertFrom-Json).models
$registered.PSObject.Properties | ForEach-Object {
    $record = $_.Value
    $asset = Join-Path (Split-Path $registry) $record.file
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $asset).Hash -ne $record.sha256) {
        throw "Unexpected asset SHA-256 for $($_.Name)"
    }
    & $py -m recommendation.delivery inspect --registry $registry --model-version $_.Name
    if ($LASTEXITCODE -ne 0) { throw "Inspect failed for $($_.Name)" }
}
```

注册表只接受本目录内的相对资产路径。加载核对归档成员、格式、内容、映射、类型、有限值、模型版本、数据版本、构建源码指纹及内部热门后备身份。同一 dataset_id 下加载多个模型时还要共享协议、拟合时间和快照哈希。三个资产各自自包含热门后备，可以单独加载。资产含可关联的匿名用户 ID、已购集合和映射，应按项目受控数据分发与存取。SHA-256 证明收到的字节与可信参考一致，参考清单本身需要可信渠道。使用 `allow_pickle=False` 的 JSON/数值归档，只给服务操作员模型目录读权限，按发布流程更换目录并重启。

`save_asset` 同目录临时写入、试加载后原子替换。确需复现冻结构建时，用合格样本和空目录执行 `delivery build`，该命令使用已冻结配置，只拟合训练加验证。样本校验、参数来源和生产源码指纹会写入元数据。构建时间和代码身份可能使新归档哈希不同，可用排序指纹检验行为；本次签收以表中原发布资产为准。 [资产格式说明](P0-REC-04A-算法模块交付.md)

## 3. 使用 Python

```python
from recommendation.engine import RecommendationEngine

engine = RecommendationEngine.from_registry(
    "artifacts/p0-rec-04a/release-assets/registry.json"
)
version = "itemcf_v1"
dataset_id = engine.describe(version)["model"]["dataset_id"]
options = {"dataset_id": dataset_id, "model_version": version, "limit": 10}
result = engine.predict(user_id="local-demo-user", **options)
batch = engine.predict_batch(["local-demo-user", "local-demo-b"], **options)
```

同一引擎应启动时加载，之后复用。也可用 `from_registry(path, preload=False)` 后在接流量前逐个 `load(version)`。批量输入为用户 ID 的可迭代对象，按输入顺序保留重复请求，批次中一个无效请求会抛出 `AssetError`，用 `error.code` 判断。Python `limit` 默认 10。CLI `predict` 输出单用户结果；CLI `batch --users-json` 接收最多 10,000 个字符串 ID，输出汇总计数和耗时。HTTP 接口为单请求。调用时不会读取原 Parquet 或训练。

## 4. 启动 HTTP 并发起请求

同一台机器的第一个终端启动服务，按 Ctrl+C 停止。命令行只接受 IPv4/IPv6 环回绑定；操作员在启动时指定注册表。启动预加载失败时进程报错退出，发布系统应检查进程退出状态与就绪端点。

```powershell
& $py -m recommendation.http_service --registry $registry --host 127.0.0.1 --port 9001
```

在第二个终端选择同一注册表路径。

```powershell
$registry = 'artifacts/p0-rec-04a/release-assets/registry.json'
$models = (Get-Content -Raw -Encoding UTF8 -LiteralPath $registry | ConvertFrom-Json).models
$base = 'http://127.0.0.1:9001'
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/ready"
$body = @{
    user_id = 'local-demo-user'
    dataset_id = $models.itemcf_v1.dataset_id
    model_version = 'itemcf_v1'
    limit = 10
} | ConvertTo-Json -Compress
Invoke-RestMethod "$base/predict" -Method Post -ContentType 'application/json' -Body $body |
    ConvertTo-Json -Depth 6
```

`GET /health` 和 `GET /ready` 在就绪时均返回 `status="ready"`、`model_versions`、`model_load_seconds`。未就绪的应用路径返回 503，Uvicorn 启动失败时可能无法建立连接。

`POST /predict` 的四个字段都必填。user_id 是 1–128 字符的原始字符串，首尾空白和控制符无效；dataset_id 是 1–128 字符且与模型绑定值精确一致；model_version 是 1–64 字符的登记名；limit 是 1–10 的整数。字符串数字、布尔整数、其他字段都无效。成功 JSON 回显三个标识及 `items`，例如以下合成示意（真实版本键取注册表）。

```json
{
  "user_id": "local-demo-user",
  "dataset_id": "example-local-id",
  "model_version": "itemcf_v1",
  "items": [
    {"item_id": "example-item", "category_id": "", "rank": 1, "score": 4.0,
     "reason": "训练截止前无用户历史，使用购买热门兜底"}
  ]
}
```

rank 从 1 连续编号，最多返回 limit 件且去重。未知类目为空字符串。score 是有限数，含义随条目来源而变。结果按 rank 展示，尤其应保留 ItemCF/BPR 个性化条目之后的热门补位顺序。分数不能用于跨模型重排或估算购买概率。无候选时为 HTTP 200 和空数组，响应头 `X-Recommendation-Status` 为 `empty_candidate_catalog` 或 `no_eligible_items`。响应体上限为 1 MiB。

| HTTP | error.code | 情形 |
| ---: | --- | --- |
| 400 | invalid_json | JSON 解析失败 |
| 422 | missing_field | 缺任一字段 |
| 422 | invalid_user_id / invalid_limit / invalid_request | 用户、条数或其他请求字段不合法，包括额外路径字段 |
| 404 | unknown_model | 版本未登记 |
| 409 | dataset_mismatch | 当前模型与数据版本冲突 |
| 503 | model_not_loaded / model_data_missing / model_corrupt / model_identity_mismatch | 服务未就绪或资产不可用；预加载同类异常会使启动失败 |
| 500 | model_invalid_output / inference_error | 模型返回违反协议或出现意外异常 |
| 其他路由状态 | http_error | 框架层路由错误 |

错误体格式为 `{"error":{"code":"dataset_mismatch","message":"Dataset does not match the selected model"}}`。真实调用方可按状态和 code 处理。

## 5. 真样本复核、性能与交接条件

样本只在受控位置使用。下面的 audit 输出聚合数据说明，validate 只评分验证窗口，http_check 以既有模型验证冻结请求排序及真实 TCP。每次准备新输出目录。`http_check` 还会写本机用户请求与结果示例，分享时只取聚合报告。保持 Python 优化关闭，以便检查断言全部执行。

```powershell
$sample = (Resolve-Path '../交接包_张凯博_2026-11-20/data/processed/events_sample.parquet').Path
$stamp = Get-Date -Format yyyyMMdd-HHmmss
& $py -m recommendation.run audit --sample $sample --out-dir "artifacts/rec06-audit-$stamp"
& $py -m recommendation.itemcf_research validate --sample $sample --out-dir "artifacts/rec06-val-$stamp"
& $py -m recommendation.http_check --sample $sample --registry $registry --out-dir "artifacts/rec06-http-$stamp"

# 仅当需要按冻结配置复现构建时执行，使用全新目录。
& $py -m recommendation.delivery build --sample $sample --out-dir "artifacts/rec06-build-$stamp"
```

现有独立模块已支持三模型接口、统一推荐响应、确定性排序、冷启动和热门补位、受控加载与批量推理。本轮单进程本机启动约 3.67 秒，8 并发混合 P95 约 16.85 ms，1,320 个计时请求错误 0，实测峰值工作集约 385.15 MiB。模型文件合计约 22.07 MiB，常驻服务内存还含 Python、解析状态和三个模型。BPR 有效购买用户路径需要计算与排序，ItemCF 的块内相似度受交互密度影响。部署资源以目标环境评测为准。 [性能口径](P0-REC-06-推荐算法最终技术报告.md)

当前 dataset_id 为 `local-parquet-sha256-7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771`，训练截止为 2019-11-21 20:24 UTC。集成人员需完成：

1. 用实际入库批次文件 SHA-256 和导入记录核验来源、时间边界、ID、事件语义与目录一致。以业务正式 dataset_id 建立经审批且可重现的版本登记；数据不同须生成相容资产。保留模型和数据身份校验。
2. 在受控账号权限下部署，业务后端传递已验证的 user_id、dataset_id、model_version、limit，处理 4xx/5xx 并按 rank 展示。单机同机调用用环回，跨主机拓扑、权限与传输保护由集成人员设计。
3. 在目标环境测启动、就绪、回滚、网络延迟、峰值并发与端到端契约。算法侧本机性能已实测，正式网站入库、业务联调和上线由集成人员验收。
