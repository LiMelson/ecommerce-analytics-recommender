# P0-REC-04A 算法模块独立交付

本轮提供纯 Python 的 RecommendationEngine，以及自包含模型资产和独立构建、加载、查询、批量推理入口。模型推理继续调用原 Popular、ItemCF、BPR 的 recommend。七类用户的加载前后与批量结果一致，完整冻结请求回归的证据见 [工程验证报告](../recommendation/reports/p0-rec-04a-verification.json)。

## 为什么还要统一封装

三个算法各自能生成排名，但它们的模型保存方式、分数含义和状态结构不同。统一组件负责版本核验、读取资产、统一响应和错误处理，让调用方用同一个接口选择已登记模型。它保持算法自身生成的顺序，将结果转换成项目交付协议。

训练使用历史事件构建热门榜、邻居或向量。推理读取已经构建好的状态，给指定用户生成结果。模型文件是这些状态与身份信息的集合。文件中的用户和商品字符串映射把矩阵行号对应回原 ID，少了映射就可能把一行向量当成另一位用户或商品。

模型版本表示一次明确登记的规则或模型，例如 popular_v1、itemcf_v1、bpr_mf_buy_v1。数据版本表示模型所绑定的数据。当前交付使用由样本 SHA-256 生成的 local-parquet-sha256-… 本地身份；正式网站 ID 仍须在真实入库后登记并核验。加载后校验身份，可以避免把旧目录、旧向量或旧已购集合用于另一份数据。

## Git 接管与本轮文件

开工时在 feat/p0-rec-03、HEAD 34a01e6。审查包 SHA-256 为 dcc726f41982c641b8047bf5e036651e6a04e9d85e6a51806c7a8caa955a4932。对应的仓库文件逐字节匹配，没有冲突或额外工作。按授权提交已审查的九个工作文件，本地提交为 **0d25a9f131235c9a011a05e01928dca034db2b0f**，再创建 feat/p0-rec-04a。main 仍为 7a6d85a。阶段三的报告和模型文件均保留。

| 文件 | 作用 |
| --- | --- |
| recommendation/assets.py | 自包含 Popular/ItemCF/BPR 资产格式、校验和原子保存 |
| recommendation/engine.py | 统一请求、显式版本登记、加载、批量调用和内部来源追溯 |
| recommendation/delivery.py | build、inspect、load、predict、batch 命令 |
| recommendation/delivery_check.py | 真实用户类型回归、冻结请求指纹和独立进程性能测量 |
| recommendation/tests/test_delivery.py | 本轮 22 个参数化测试用例 |
| recommendation/reports/p0-rec-04a-verification.json | 聚合验证、模型大小与性能结果 |
| recommendation/reports/p0-rec-04a-metadata.json | 公开可审查的版本、参数、来源代码与资产哈希，不含用户映射 |
| recommendation/README.md | 当前交付入口与测试数量 |
| 本文 | 算法模块交接、接口、人工示例与复现说明 |

原模型算法源码、bpr_io.py、Benchmark、标签、参数配置及历史正式报告保持原内容。本轮新增的算法资产都在 artifacts/p0-rec-04a/ 内，由仓库忽略规则排除。审查包只有源码、测试、聚合证据和补丁。

## Python API 和协议

~~~python
from recommendation.engine import RecommendationEngine

engine = RecommendationEngine.from_registry(
    "artifacts/p0-rec-04a/release-assets/registry.json"
)
dataset_id = engine.describe("itemcf_v1")["model"]["dataset_id"]

result = engine.predict(
    user_id="local-demo-user",
    dataset_id=dataset_id,
    model_version="itemcf_v1",
    limit=10,
)
batch = engine.predict_batch(
    ["local-demo-a", "local-demo-b"],
    dataset_id=dataset_id,
    model_version="itemcf_v1",
    limit=10,
)
~~~

响应只有 user_id、dataset_id、model_version、items，条目只有 item_id、category_id、rank、score、reason。rank 从 1 连续递增，空候选返回空数组。未知类目返回空字符串。每条解释只描述实际使用的热度、余弦、BPR 信号或兜底原因。

内部 predict_with_trace 返回响应与单独的 source、item_sources、fallback_reason。对外 predict 不加入这些字段。predict_batch 与逐用户调用具有同样顺序和检查，空批次也校验模型和参数。批次一旦遇到无效请求会抛出明确异常，不返回不完整的成功列表。

注册表明确列出每个版本所指向的本地文件、完整文件 SHA-256、数据 ID、模型类型和生产源码指纹。路径必须位于注册目录中。predict 从已经加载的模型取值，不从用户传入的字符串拼接路径，也不在请求中训练。可用 from_registry(preload=False) 后逐个 load(version)，便于单独加载指定模型。describe 返回元数据副本，调用方修改它不能影响模型。

### 可以手算的混合示例

三位用户，甲买 A、浏览 B，乙浏览 A、C，丙浏览 C、E 并购买 D 五次。ItemCF 的 A 到 B 相似度为 1/√2，A 到 C 为 1/2。重复购买不放大二值交互。甲以 A、B 为种子时，A 因已购被过滤，B、C 分别得约 0.7071 和 0.5。

还需要第三件商品时，热门榜中 D 的购买次数是 5。最终条目仍是 B、C、D，对应 rank 1、2、3。分数 0.7071、0.5、5 的单位不同。统一组件保持这份顺序，后续展示按 rank，不按混合 score 再排序。余弦、BPR 内积及购买次数均不表示购买概率。

## 保存、加载与兼容检查

每个 .asset 都可独立加载，内含热门兜底、已购集合、目录、类目、元数据和必要的主模型状态。加载不依赖 Parquet、数据库或重新训练。

- Popular 保存完整热度排序、商品与类目、用户事件次数、用户已购集合、训练起止及内容身份。
- ItemCF 另存用户和商品映射、二值交互 CSR 及截断后的相似邻居 CSR，参数记录 80 邻居和最近 100 件商品。
- BPR 直接复用既有 bpr_io.py，将安全数值模型作为内部成员，外层补上自包含的 Popular 状态。参数保持 16 维、40 Epoch、学习率 0.05、正则 0.01、每正样本三个负例、seed=20261120。

资产外层包含格式版本、模型类型、模型和数据身份、训练内容 SHA、拟合起点和截止点、参数、生产源码与依赖版本、构建时间和成员校验和。加载校验注册表整文件哈希、内部成员哈希、元数据及兜底身份。CSR 校验索引范围、行偏移、重复边、自环和有限值；BPR 原有加载器校验向量形状、映射、信号范围和身份。数值文件使用 allow_pickle=False。已加载状态继续使用只读集合和不可变矩阵缓冲区。

save_asset 使用同目录临时文件完成写入，并成功试加载验证后才原子替换目标文件。写失败、验证失败或文件中断时，前一个有效文件保留。build 要求新的空目录，只有全部资产保存成功才写 registry.json。失败加载不会替换引擎里已加载的有效模型。

生产源码指纹记录资产构建时的代码。注册表固定这个身份，加载器按自身声明支持的格式校验。历史实验报告中的旧源码指纹仍作为历史证据保留。

## 错误语义

| 情况 | 结果或错误代码 |
| --- | --- |
| 空白、超长、带控制字符或非字符串用户 | invalid_user_id |
| 未登记版本或版本别名冲突 | unknown_model / model_identity_mismatch |
| 登记但尚未 load | model_not_loaded |
| 请求数据与加载模型冲突 | dataset_mismatch |
| 文件、成员、映射或校验和损坏 | model_corrupt |
| 注册表或注册资产文件缺失 | model_data_missing |
| limit 不是 1–10 的整数 | invalid_limit |
| 字符串被误当作整批用户列表 | invalid_batch |
| 模型返回重复、越界、已购、非法分数 | model_invalid_output |
| 没有候选 | items=[]，内部标为 empty_candidate_catalog |
| 完全新用户 | 同截止点热门兜底，reason 和内部来源明确 |
| 有历史但无 BPR 购买正反馈或因子 | 原模型规定的热门兜底，来源明确 |

模型损坏、不可用和身份冲突都会失败。正常冷启动和缺少可训练偏好仍沿用冻结的兜底政策。

## 真实回归与历史实验保护

构建使用已验证 SHA-256 为 7f6fe3cf0dee62e04665b09e921be290e127f947e4f6baf97837549cc42cf771 的交接样本。只用 2019-10-01 00:00 至 2019-11-21 20:24 UTC 的 929,827 条历史事件构建最终快照。内容指纹为 95a6fc7a551b2385beac77fa09783ed45d2de9dd7b003e7a725db0f6e6ab1f02。参数从已冻结报告读取并与原配置交叉检查，未再选择参数。

七类代表均从拟合历史中找到适用用户，新用户使用明确本地测试 ID。真实 ID 没有写入报告。

| 用户类型 | Popular | ItemCF | BPR |
| --- | --- | --- | --- |
| 完全新用户 | 热门兜底 | 热门兜底 | 热门兜底 |
| 仅浏览 | 热门 | 余弦推荐 | 无购买热门兜底 |
| 加购无购买 | 热门 | 余弦推荐 | 无购买热门兜底 |
| 已购买 | 已购过滤 | 余弦及已购过滤 | 因子及已购过滤 |
| 高频 | 热门 | 余弦推荐 | 因子推荐 |
| 有复购历史 | 已购过滤 | 已购过滤 | 已购过滤 |
| 无相似邻居 | 热门 | 无邻居热门兜底 | 无购买热门兜底 |

每类三模型都完成原始内存、保存加载、连续不同用户和批量调用的对照。全部返回十件商品且结果一致。

另使用已冻结的 15,158 个测试请求用户 ID，将三模型新构建的 recommend 来源和商品顺序算成整批 SHA-256，与原实验排序指纹逐值匹配。统一引擎的加载前后逐用户响应也一致。本轮未重算测试购买指标。由于顺序、来源、原标签及分母都保持相同，原基线仍适用。

| 模型 | 原主 Recall@10 | 原主 NDCG@10 |
| --- | ---: | ---: |
| Popular | 0.17365 | 0.10100 |
| ItemCF | 0.14113 | 0.07908 |
| BPR | 0.13775 | 0.07727 |

## 独立进程性能

| 模型 | 文件字节 | 加载秒 | 加载后工作集 MiB / 增量 | 单次冷启动 ms | P50 / P95 ms | 批量100人秒 | 批量1000人秒 |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: |
| popular_v1 | 522,185 | 0.2459 | 151.12 / +45.20 | 0.0578 | 0.0065 / 0.0077 | 0.000989 | 0.007289 |
| itemcf_v1 | 11,877,228 | 0.8660 | 216.28 / +109.91 | 0.0659 | 0.1429 / 1.4234 | 0.010118 | 0.338816 |
| bpr_mf_buy_v1 | 10,739,918 | 0.3677 | 159.39 / +53.31 | 0.0697 | 0.0116 / 3.4740 | 0.001565 | 0.543856 |

每个模型在一个新 Python 子进程内只加载该模型。加载计时包含注册信息和文件校验。内存为 Windows 实际工作集，另存加载前基线、增量和进程峰值。单次延迟是新用户冷启动请求；P50/P95 来自一名新用户加按字符串 ID 排序的前 999 名历史用户，串行测量。100/1000 用户的批量接口另计耗时。该样本包含热门兜底，不能当作全购买用户或生产流量分布。三模型异常及空结果数量均为 0。

## Windows PowerShell 与 VSCode

用 VSCode 打开仓库根目录，将 Python 解释器选为 .venv/Scripts/python.exe。在集成 PowerShell 中先建环境与验证。

~~~powershell
py -3.12 -m venv .venv
$env:PYTHONUTF8 = '1'
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -r recommendation/requirements.txt
& $py -m pytest -q recommendation/tests
~~~

构建是离线操作，需要同版私有交接样本。创建新目录保留每次资产，参数读取冻结配置。

~~~powershell
$sample = '..\交接包_张凯博_2026-11-20\data\processed\events_sample.parquet'
$out = "artifacts/p0-rec-04a/build-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $py -m recommendation.delivery build --sample $sample --out-dir $out

$registry = "$out/registry.json"
& $py -m recommendation.delivery inspect --registry $registry --model-version itemcf_v1
& $py -m recommendation.delivery load --registry $registry --model-version bpr_mf_buy_v1
~~~

推理只需要注册表及资产，不传训练数据。以下最后一条 Python 命令直接返回协议 JSON。

~~~powershell
$record = Get-Content -Raw -Encoding UTF8 $registry | ConvertFrom-Json
$dataset = $record.models.popular_v1.dataset_id
& $py -m recommendation.delivery predict --registry $registry --user-id local-demo-user --dataset-id $dataset --model-version itemcf_v1 --limit 10
~~~

批量 CLI 输出数量、空结果及耗时汇总。需要逐用户响应时使用 Python predict_batch。

~~~powershell
@('local-demo-a','local-demo-b') | ConvertTo-Json | Set-Content -Encoding UTF8 "$out/users.json"
& $py -m recommendation.delivery batch --registry $registry --model-version bpr_mf_buy_v1 --dataset-id $dataset --users-json "$out/users.json" --limit 10
~~~

完整工程回归命令会在另一个新目录中构建真实资产，输出代表类型、完整冻结请求回归及独立加载性能。它不调用测试指标评估器。

~~~powershell
$verify = "artifacts/p0-rec-04a/verify-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $py -m recommendation.delivery_check verify --sample $sample --out-dir "$verify/assets" --report "$verify/report.json"
~~~

首次完整工程回归后，补充了异常 JSON 布局和空批次身份检查，并在最终源码上重新构建、验证。两次的用户类型和冻结请求指纹一致。84 项推荐测试通过，含本轮 22 项，Ruff check 和 format --check 通过，data_algo 33 项通过。所有检查退出码为 0。审查包从解压目录再次执行推荐测试，运行入口和结果保留于 review/。

## 后续要求与边界

04B 才提供独立 HTTP /predict 适配，包括请求校验到 HTTP 状态映射、启动预加载、超时与并发测试。正式网站 dataset_id 绑定、注册发布和端到端页面验收需要后续明确联调。本轮没有完成 HTTP 性能验证、网站模型发布或完整任务服务。当前指标证据仍属于匿名离线样本，缺少库存和线上曝光记录的限制继续存在。
