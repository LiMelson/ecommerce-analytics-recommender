# P0-REC-01 工程审查与实验验收

## 2026-11-20 开工检查

| 检查 | 实际证据 |
| --- | --- |
| Git 初始状态 | 仓库位于工作区的同名子目录。当前分支为 main，工作区干净，main、origin/main 和远端 main 均为 7a6d85addd92d3ce0d55259f77dc6b13e6e93bce。 |
| PR #2 | 开工查询时 GitHub 显示开放、未合并、可合并，base 为 7a6d85a，head 为 a45ae5545cc7e40a2905d41a90c9fb22a90e5c6b。Git 抓取的 PR head 与 API 一致，由 2fecf5e 与 a45ae55 两个提交组成，差异为 40 个文件。main 是其历史祖先。 |
| 交接核对 | 逐个独立验证 SHA256SUMS.txt 中的 35 个文件。与 PR 或基线共同存在的 33 个文件中，24 个字节完全一致，9 个仅换行符不同。包中另有未追踪的 HANDOVER.md 与真实样本。 |
| 工作分支 | 开工时使用 Git 在 PR head 创建 feat/p0-rec-01。最终本地分支切换到合并版基线，原本地 main 引用仍为 7a6d85a。 |
| 正式资料 | 仓库及工作区的项目需求文档二进制哈希相同。核对了 FR07、FR08、第 7–11 章及 AT07、AT08、AT09、AT12，并以仓库交付约定核对当前接口。 |

PR 状态来源为 [GitHub PR #2](https://github.com/LiMelson/ecommerce-analytics-recommender/pull/2)。主要 Git 命令如下。如 Git 直连超时，使用本机系统代理进行单次抓取。

~~~powershell
git status --short --branch
git log -6 --oneline --decorate
git branch -avv
git fetch --no-tags origin refs/pull/2/head:refs/remotes/origin/pr/2
git rev-parse main origin/pr/2
git log --oneline main..origin/pr/2
git diff --name-status main...origin/pr/2
git merge-base --is-ancestor main origin/pr/2
git switch --no-track -c feat/p0-rec-01 origin/pr/2
~~~

最终复查时，PR #2 已于 2026-11-20 18:01:13（北京时间）合并。最终 head 为 ac51ca6b1e73b19b3ab60a1c6fa37ae47a4f8249，远端 main 为 squash 提交 6735a2ac2d3542cff855889cb615ca103e6864f1，两者文件树一致。与开工快照相比，新增提交改了六个 data_algo 文件，涉及安全完成输出、时间戳精度及测试。抽样条件保持一致，项目的前后端和工作流文件相同。

确认上游六个改动路径与本轮工作文件互不重叠后，将初始本地分支改名为 backup/p0-rec-01-pre-merge，在 origin/main 上创建同名工作分支 feat/p0-rec-01。切换前后 14 个任务工作文件逐字节相同。按合并版重新执行数据验收、推荐用例和两次真实 Benchmark，数据验收和 Benchmark 聚合结果均与开工快照一致。最终证据指向合并版。

~~~powershell
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main refs/pull/2/head:refs/remotes/origin/pr/2-final
git diff --name-status origin/pr/2 origin/main
git diff --exit-code origin/pr/2-final origin/main
git branch -m backup/p0-rec-01-pre-merge
git switch --no-track -c feat/p0-rec-01 origin/main
git rev-parse HEAD main origin/main
~~~

| 区域 | 接管前状态与本轮影响 |
| --- | --- |
| frontend | Vue/Vite 已经调用 FastAPI，推荐未就绪时显示后端状态。已有 Node 数据逻辑测试、Vitest UI 测试及构建检查，本轮没有前端变更。 |
| backend | 已有模型查询、POST /models、POST /models/{id}/activate、GET /recommendations。推理使用配置好的 URL、五秒超时和 1 MiB 上限，并检查三项身份字段、排序与排重。训练任务的需求与当前导入或导出任务属于不同功能。本轮复用已有响应校验模型，后端、激活流程、迁移和数据库应用代码均无变更。 |
| data_algo | main 上尚无此模块，PR #2 提供数据清洗、抽样、统计及网站导出。分支继承 PR 的代码、文档、测试和独立 CI，本轮没有编辑。 |
| recommendation | main 和 PR #2 上只有 README。本轮在此新增核心、命令、配置、测试和聚合证据，更新 README 是唯一与 PR #2 触及范围重叠的编辑。 |
| 协作风险 | 后续数据契约变更时应复查样本哈希和导出结果。组长负责网站版本、服务接入与模型登记，何彦彬负责数据派生和导出，吴文森负责页面及联调。本轮工作可在独立分支审查。 |

原有 Project checks 在 main 相关事件上运行前端及后端检查，后端 CI 含 MySQL 8.4 与 ClickHouse 26.8.37.168。PR #2 另外引入按路径触发的 data-algo CI。后端锁定 PyArrow 23.0.1，数据组锁定 PyArrow 26.0.1，因此验证时使用独立虚拟环境。推荐集仍复用数据组依赖。样本和本地产物由根 .gitignore 排除，报告目录只保存统计聚合和指纹。

## 真实样本热门结果

固定 61 天观测区间，按经过时间的 70% / 15% / 15% 切分。表内各“已知”列按去重商品统计，开始目录是该窗口开始前全部已知商品，可包含没有在该窗口出现的商品。

| 窗口（UTC，左闭右开） | 事件 | 用户 | 商品 | 购买事件 | 购买用户 | 首现用户 | 开始目录 | 本窗口商品在初始训练期见过 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10-01 00:00 到 11-12 16:48 | 627,023 | 39,236 | 60,817 | 10,276 | 4,624 | 39,236 | 0 | 60,817 |
| 11-12 16:48 到 11-21 20:24 | 302,804 | 18,438 | 40,513 | 4,180 | 2,251 | 7,715 | 60,817 | 26,003 |
| 11-21 20:24 到 12-01 00:00 | 149,527 | 15,158 | 27,617 | 2,223 | 1,250 | 5,736 | 75,327 | 17,923 |

| 窗口与口径 | 评估用户 | Recall@10 | NDCG@10 | Precision@10 | HR@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 验证，可达购买目标 | 2,069 | 0.13917 | 0.08054 | 0.02001 | 0.18076 |
| 验证，全购买目标 | 2,251 | 0.12507 | 0.07285 | 0.01839 | 0.16615 |
| 测试，可达购买目标 | 1,122 | **0.17365** | **0.10100** | 0.02273 | 0.20856 |
| 测试，全购买目标 | 1,250 | 0.15322 | 0.08981 | 0.02040 | 0.18720 |

验证主覆盖率为 14 / 60,817（0.02302%），全用户覆盖为 15 / 60,817。测试两种覆盖率均为 16 / 75,327（0.02124%）。覆盖率很低，符合所有人共享热门榜且只做已购过滤的排序逻辑。本次没有曝光记录，因此只能据此评价离线排序。完整结果包含原始精度、分组指标、来源、覆盖及版本信息。 [真实运行 JSON](../recommendation/reports/p0-rec-01-benchmark.json)

测试期 15,158 人中，7,794 人在初始训练期已见，1,628 人仅在验证期补齐历史，5,736 人在最终拟合窗口仍无历史。14,036 人无有效目标，其中 13,908 人没有购买，128 人的购买目标全部不可达。测试去重购买对共 1,798 个，含可达的 1,561 对、被过滤的复购 173 对（286 条事件）、未来新商品的 64 对（69 条事件，占全部购买对 3.56%）。测试首现商品有 6,366 件，其中 64 件被购买。验证期首现 14,510 件，其中 190 件被购买，对应 193 个购买对和 219 条购买事件。

| 测试分组 | 窗口用户 | 购买用户 | 有效用户 | 主 Recall@10 | 主 NDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 冷启动 | 5,736 | 306 | 297 | 0.24555 | 0.14573 |
| 低频 | 1,253 | 54 | 53 | 0.22013 | 0.14791 |
| 中频 | 3,156 | 266 | 246 | 0.17202 | 0.09516 |
| 高频 | 5,013 | 624 | 526 | 0.12912 | 0.07376 |

## 执行环境与复现证据

Windows 11，Python 3.12.6，Intel Core i9-14900HX，32 逻辑处理器，物理内存约 31.80 GiB。PyArrow 26.0.1、pandas 3.1.1、NumPy 2.5.5、DuckDB 2.0.0、SciPy 1.18.1、pytest 9.2.0。最终基线提交为 6735a2ac2d3542cff855889cb615ca103e6864f1，未提交的推荐源码与配置指纹为 28c7324be7d73413e233d6a4921cc95b5f3f3bfac1cdc11375a939fcb5f4cad7。指纹涵盖运行模块、推荐依赖入口及实际配置的规范化文本，由命令自动生成。环境与各阶段耗时保留在两份 JSON 中。

数据验收读入与校验 0.36 秒，验收 5.97 秒，峰值工作集约 854 MiB。Benchmark 读入与校验 0.36 秒，验证构建 0.82 秒，验证评估 0.65 秒，最终重训 1.22 秒，测试评估 0.57 秒，总计约 5.64 秒，峰值工作集约 1,008 MiB。最终榜单在单进程、串行、模型已预构建条件下测试 15,158 次，单次推荐 P50 约 0.0008 ms，P95 约 0.0019 ms，仅测量内存核心调用。网站端到端延迟待 HTTP 服务接入后测量。

第二次使用 PYTHONHASHSEED=91 运行，排除了哈希顺序对结果的影响。两次 result 和 provenance 完全相等，包括验证与测试窗口逐请求整批排序指纹。测试排序指纹为 7a613ab215451f01ada3a07701587aed4e5b589ebe9a30aec9e5c08e6005fd72。记录运行时间的 runtime 自然会变化。命令见 [PowerShell 复现步骤](../recommendation/README.md)。

## 测试证据与验收界限

| 目录与命令 | 实际结果 |
| --- | --- |
| 根目录：.\.venv\Scripts\python.exe -m pytest -q recommendation/tests | 16 passed。人工构造数据覆盖切分边界、候选拒绝、同分排序、冷启动、ID 精度、用户切换、空推荐、无购买、未来商品、复购、分组、版本与复跑。 |
| 根目录：.\backend\.venv\Scripts\ruff.exe check --config backend/pyproject.toml recommendation 及同配置的 format --check | 通过。 |
| data_algo：..\.venv\Scripts\python.exe -m pytest -q -rs | 开工快照 30 passed，合并版 33 passed，含后端入库格式兼容测试。 |
| backend：.\.venv\Scripts\ruff.exe check app tests migrations 与 .\.venv\Scripts\ruff.exe format --check app tests migrations | 通过，22 个文件已格式化。 |
| backend：.\.venv\Scripts\python.exe -m pytest -q -rs | 20 passed，4 skipped。四个跳过项为默认未启用的真实数据库或可选规模测试。 |
| backend：配置本机隔离 DB，设置 RUN_DB_TESTS=1 后运行同一条 pytest 命令 | 23 passed，1 skipped。三项隔离 DB 用例通过。可选的百万条生成数据负载未启用。现有 Starlette 对 httpx 的弃用告警共 1 条。 |
| frontend：npm.cmd ci、npm.cmd test、npm.cmd run test:ui、npm.cmd run build | 依赖安装成功，6 个数据逻辑测试与 5 个 UI 测试通过，生产构建通过。 |
| 真实样本推理协议 | 按测试截止点构建内存模型，传入匿名本地测试用户的请求，将 JSON 交给后端原有 RecommendationResult 校验。三字段、10 件去重商品和连续 rank 校验通过。 |

数据库回归采用项目 CI 的同版镜像 MySQL 8.4、ClickHouse 26.8.37.168，两库临时绑定 127.0.0.1 的 43306、48123。测试使用随机 commerce_test_* 库，结束后清理。本次没有运行线上迁移。复跑可用以下命令，从 backend 目录执行。

~~~powershell
docker run -d --rm --name p0-rec-01-regression-mysql -p 127.0.0.1:43306:3306 -e MYSQL_ROOT_PASSWORD=ci-mysql-ephemeral-only -e MYSQL_DATABASE=commerce_ci mysql:8.4
docker run -d --rm --name p0-rec-01-regression-clickhouse -p 127.0.0.1:48123:8123 -e CLICKHOUSE_DB=commerce_ci -e CLICKHOUSE_USER=commerce -e CLICKHOUSE_PASSWORD=ci-clickhouse-ephemeral-only -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 --memory=3g --cpus=2 clickhouse/clickhouse-server:26.8.37.168
docker exec p0-rec-01-regression-mysql mysqladmin ping -h 127.0.0.1 --silent
docker exec p0-rec-01-regression-clickhouse clickhouse-client --user commerce --password ci-clickhouse-ephemeral-only --query 'SELECT 1'
$env:MYSQL_HOST='127.0.0.1'
$env:MYSQL_PORT='43306'
$env:MYSQL_USER='root'
$env:MYSQL_PASSWORD='ci-mysql-ephemeral-only'
$env:MYSQL_DATABASE='commerce_ci'
$env:CLICKHOUSE_HOST='127.0.0.1'
$env:CLICKHOUSE_PORT='48123'
$env:CLICKHOUSE_USER='commerce'
$env:CLICKHOUSE_PASSWORD='ci-clickhouse-ephemeral-only'
$env:CLICKHOUSE_DATABASE='commerce_ci'
$env:RUN_DB_TESTS='1'
.\.venv\Scripts\python.exe -m pytest -q -rs
docker stop p0-rec-01-regression-clickhouse p0-rec-01-regression-mysql
Remove-Item Env:RUN_DB_TESTS
~~~

- **FR07 / AT07** 本轮验证内存推荐核的用户切换、候选排重、已购过滤和热门兜底。网站还需要接入推理服务后做页面端到端验收。
- **FR08 / AT08** 本轮验证了请求版本不匹配时拒绝，样本哈希与模型元数据对应。现有后端有数据与模型兼容检查。本地 ID 仅供测试，正式网站导入和模型登记仍待联调。
- **AT09** 后续训练任务需验收真实阶段、失败、中断、取消及成功状态。当前提供可复现的离线命令。
- **AT12** 热门已通过统一评估器跑出真实结果。ItemCF、BPR 和多模型比较须沿用该协议。

## ItemCF 开发建议

1. 先在验证窗口对照同一版热门。构造截止前的用户商品稀疏交互矩阵，比较简单共现与余弦，分块保存 TopN 邻居。购买稀疏时可在验证上比较多行为输入。保存权重、邻居数、截断规则和资源实测。
2. 候选、已买过滤、分母、冷启动分组和测试重训规则全部复用。冷启动与无可用邻居分别标记热门兜底，报告兜底比例、高低频结果与覆盖率。特别核对高频用户表现以及截断带来的召回损失。
3. 复购和未见商品分别影响 173 与 64 个测试用户商品对。为避免误读模型之间的差异，同时报告可达主口径和全购买目标口径。业务若决定支持复购，先出新协议并用新分母重跑所有模型。
4. 用户选择为单站取模样本，两个月内的行为存在窗口截断，缺少库存状态。调参使用验证窗口，已有测试结果只用于最终对照。扩大样本时应在数据组核实源文件和抽样版本后，为全部模型统一建立新实验。
