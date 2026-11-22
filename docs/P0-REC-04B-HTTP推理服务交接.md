# P0-REC-04B 独立推理服务交接

使用本地 04A 已发布资产启动 FastAPI 与 Uvicorn，提供 `POST /predict`、`GET /ready`、`GET /health`。真实 TCP 测试已覆盖三个注册模型，HTTP 响应与纯 Python 提供的完整结果一致。 [HTTP 聚合实测](../recommendation/reports/p0-rec-04b-http-verification.json)

## 从 Python 引擎到 HTTP

`RecommendationEngine` 读取已经拟合好的热度、邻居和向量，负责排序。HTTP 适配把这个 Python 调用提供给其他进程。后续调用方将四个字段发给 `/predict`，无需在自己的进程导入算法。JSON 是使用字符串、数字、对象和数组表达这些字段的文本格式。

~~~mermaid
flowchart LR
    A["调用方发送 JSON"] --> B["POST /predict 校验字段和类型"]
    C["启动时校验注册表并加载三模型"] --> D["RecommendationEngine"]
    B --> D
    D --> E["按现有顺序返回协议 JSON"]
~~~

服务启动时读取操作员指定的可信 `registry.json`，核验文件哈希、资产和后备模型的身份，然后预加载每个登记版本。预加载失败会使服务启动失败，错误码可在本地 Uvicorn 日志中看到。完整预加载后，健康和就绪端点同时返回 `status=ready`，以及加载版本和耗时。推理时复用内存模型，不读取 Parquet。启动后如需切换资产，先准备并核验正式登记的资产，再启动新进程加载。已加载模型的就绪状态由该进程的可用模型决定。

## Windows PowerShell 和 VSCode

在 VSCode 打开仓库根目录，选择 `.venv\Scripts\python.exe` 作为 Python 解释器。第一个 PowerShell 终端安装服务依赖并启动。已有推荐虚拟环境可直接复用。

~~~powershell
py -3.12 -m venv .venv
$env:PYTHONUTF8 = '1'
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -r recommendation/requirements-http.txt
& $py -m pip check

$registry = 'artifacts/p0-rec-04a/release-assets/registry.json'
& $py -m recommendation.http_service --registry $registry --host 127.0.0.1 --port 9001
~~~

`--registry` 选择可信本机注册表，资产相对文件在其目录下。`--port` 默认 9001，测试可用 0 让系统分配空闲端口。`--host` 只接受 IPv4 或 IPv6 回环地址，默认 127.0.0.1。HTTP 服务和测试依赖单列在 `requirements-http.txt`，包含 FastAPI 0.163.1、Uvicorn 0.54.0 与 HTTPX 0.28.1，普通算法调用仍使用原环境入口。启动终端按 Ctrl+C 停止服务。

打开第二个 PowerShell 终端，读取真实本地数据版本并发送一次 ItemCF 请求。

~~~powershell
$baseUrl = 'http://127.0.0.1:9001'
Invoke-RestMethod "$baseUrl/ready"

$registry = 'artifacts/p0-rec-04a/release-assets/registry.json'
$registered = (Get-Content -Raw -Encoding UTF8 $registry | ConvertFrom-Json).models
$body = @{
    user_id = 'local-http-demo-04b'
    dataset_id = $registered.itemcf_v1.dataset_id
    model_version = 'itemcf_v1'
    limit = 10
} | ConvertTo-Json -Compress

Invoke-RestMethod "$baseUrl/predict" -Method Post -ContentType 'application/json' -Body $body |
    ConvertTo-Json -Depth 6
~~~

要查看三个真实模型响应，遍历注册表中的三个正式模型版本。这里使用明确的本地合成用户，三模型都会按自己的冷启动路径取得相同训练期热门结果。

~~~powershell
foreach ($version in @('popular_v1', 'itemcf_v1', 'bpr_mf_buy_v1')) {
    $body = @{
        user_id = 'local-http-demo-04b'
        dataset_id = $registered.$version.dataset_id
        model_version = $version
        limit = 10
    } | ConvertTo-Json -Compress
    Invoke-RestMethod "$baseUrl/predict" -Method Post -ContentType 'application/json' -Body $body |
        ConvertTo-Json -Depth 6
}
~~~

一次成功响应是 HTTP 200。外层有请求中的三个身份字段及 items 数组，每项包含 item_id、category_id、rank、score、reason。三模型真实完整响应已在本机留存于 `artifacts/p0-rec-04b/localhost-final/http-examples.local.json`。它们通过了现有后端 RecommendationResult 类型校验。逐用户响应留存在本机，不随审查包分发。

`rank` 从 1 连续递增，且最多返回请求 limit 件，不重复。商品已购过滤继续由原引擎控制。ItemCF 余弦和、BPR 向量内积和热门购买次数各有自己的单位。调用方使用最终 rank 展示。模型分数不能解释成购买概率。ItemCF 混合结果的内部整体来源为 `itemcf_with_popular_fill`，补位条目来源和实际补位原因为 `popular_fill`。内部追溯字段不会写入协议响应。

## 请求和错误约定

四个请求字段均必填。`user_id` 为引擎认可的 1–128 字符字符串，不能带首尾空白或低位控制字符。`dataset_id` 为非空字符串，必须与模型已绑定的数据版本相同。`model_version` 必须是已登记标识。`limit` 为 1–10 的整数，布尔和字符串数字无效。请求不接收额外字段或文件路径。

| 场景 | HTTP | error.code |
| --- | ---: | --- |
| 无法解析的请求 JSON | 400 | invalid_json |
| 缺少其中一个必填字段 | 422 | missing_field |
| 用户 ID 无效 | 422 | invalid_user_id |
| limit 类型或范围非法 | 422 | invalid_limit |
| 其余请求类型或额外字段不符 | 422 | invalid_request |
| 模型未登记，包括旧别名 bpr_v1 | 404 | unknown_model |
| 数据版本与该模型不匹配 | 409 | dataset_mismatch |
| 模型尚未加载 | 503 | model_not_loaded |
| 资产缺失、损坏或身份冲突 | 启动失败，推理路径若遇到则为 503 | model_data_missing / model_corrupt / model_identity_mismatch |
| 模型产出违反协议 | 500 | model_invalid_output |
| 意外推理异常 | 500 | inference_error |

错误响应形如 `{"error":{"code":"dataset_mismatch","message":"Dataset does not match the selected model"}}`。PowerShell 默认会在非 2xx 响应时抛错，可在 catch 中查看 `$_.ErrorDetails.Message`。无候选是正常 200，返回 `items: []`，响应头 `X-Recommendation-Status` 区分 `empty_candidate_catalog`（空目录）和 `no_eligible_items`（如全部已购）。成功响应编码后受 1 MiB 限制。默认测试客户端以 5 秒为超时口径，HTTP 服务不依赖团队后端运行。

## 真实 HTTP 实测

进程启动至就绪约 **3.59 秒**，其中模型加载校验约 **1.51 秒**。就绪工作集 **309.1 MiB**，结束工作集 **311.4 MiB**，峰值 **385.1 MiB**。计时工作负载共 1,320 个请求，异常、超时和内容不匹配均为 0。混合 8 并发 P95 为 **12.93 ms**，最慢 **31.73 ms**，最大响应 **1,859 字节**。

| 模型 | 单次冷用户 ms | 串行 P50 / P95 ms | 8 并发 P50 / P95 ms | 最大响应字节 |
| --- | ---: | --- | --- | ---: |
| Popular | 1.093 | 0.887 / 1.163 | 4.732 / 8.171 | 1,674 |
| Itemcf | 1.061 | 0.918 / 2.289 | 5.297 / 8.032 | 1,813 |
| Bpr | 1.784 | 1.132 / 4.752 | 9.817 / 20.607 | 1,859 |

测量使用实际本机 TCP、Uvicorn 单服务进程和持久连接。每模型 120 个串行、240 个 8 并发请求，另有 240 个跨模型混合请求。用户构成是七种代表类型加 33 个固定抽样的训练历史用户。每个成功响应与同一资产直接通过 Python 引擎得到的响应逐字段比较。延迟从客户端提交请求到收齐响应体。工作集读取 Uvicorn 启动日志中的实际服务 PID，排除了 Windows 虚拟环境的启动进程。首轮采集曾取到启动进程，最终报告采用修正后复测的数据。

验证脚本只从已有资产加载。私有交接 Parquet 仅由独立的验证客户端读取，用于样本身份、代表用户选择和冻结测试请求复核。正式购买标签没有重新打分。三模型各 15,158 个冻结请求的排序与来源指纹均一致，历史正式报告保留原字节内容，资产哈希和修改时间也通过前后核对。

~~~powershell
$env:PYTHONUTF8 = '1'
$py = '.\.venv\Scripts\python.exe'
& $py -m pytest -q recommendation/tests
& .\backend\.venv\Scripts\ruff.exe check --config backend/pyproject.toml recommendation
& .\backend\.venv\Scripts\ruff.exe format --check --config backend/pyproject.toml recommendation

$sample = '..\交接包_张凯博_2026-11-20\data\processed\events_sample.parquet'
$out = "artifacts/p0-rec-04b/verify-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $py -m recommendation.http_check --registry $registry --sample $sample --out-dir $out
~~~

## 源码和 Git

| 文件 | 作用 |
| --- | --- |
| recommendation/http_service.py | 启动预加载、健康检查、HTTP 协议、错误和响应限制 |
| recommendation/http_check.py | 实际启动本机子进程，比较 HTTP 响应，测量并发并复核冻结排序 |
| recommendation/engine.py | 区分内部混合来源与具体补位原因 |
| recommendation/requirements-http.txt | 服务和 HTTP 测试依赖 |
| recommendation/tests/test_http_service.py | HTTP 契约、错误、空候选、损坏资产、并发及只读回归 |
| recommendation/reports/p0-rec-04b-http-verification.json | 纯聚合实测、原资产与排序指纹 |
| recommendation/README.md 和本文 | 源码入口和交接 |

04A 的审查包中 50 个仓库文件与预交付工作区逐字节一致。三个 04A 资产的 SHA-256 也与 04A 元数据一致。已审查的九个文件保存为本地提交 fdeeb734858f29d32e83c8a2333c801767b9d1eb，并从该提交创建 feat/p0-rec-04b。04B 工作区补丁和 Git 状态随审查包交付。服务 34 项及原有 84 项推荐测试共 118 项通过。现有后端契约 1 项、数据组 33 项及 Ruff 都通过。Starlette TestClient 有现存 HTTPX 弃用警告。实测使用独立服务进程。

## 后续集成核验

当前数据身份为 `local-parquet-sha256-7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771`。它对应本地交接样本 SHA-256，训练截止为 2019-11-21 20:24 UTC。正式网站数据版本绑定和业务发布由后续集成人员完成。

集成人员先核对正式入库数据的来源、SHA-256、训练窗口和用户商品 ID 一致性，再通过正式流程生成并登记与那个 dataset_id 匹配的资产。使用资产元数据和本轮聚合报告中的样本、快照及资产指纹核验。随后为既有后端配置完整上游 URL，确认其发送的 user_id、dataset_id、model_version、limit 与服务登记一致，消费 items 时按 rank 展示。正式环境的并发、网络延迟和端到端验收仍待联调。 [现有数据与算法交付约定](../docs/数据与算法交付约定.md)
