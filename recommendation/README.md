# 推荐算法与实验

P0-REC-03 的 BPR-MF 训练、模型保存加载、三模型统一结果和 Windows 命令见 [BPR 实验与复现](../docs/P0-REC-03-BPR实验与复现.md)。

P0-REC-02 的 ItemCF、真实对照结果、手算案例和 VSCode 复跑说明见 [ItemCF 对照实验与复现](../docs/P0-REC-02-实验与复现.md)。

P0-REC-01 在真实交接样本上验收数据，并建立未来购买 Benchmark 与热门基线。统一候选、标签和分组后，后续 ItemCF、BPR 才能与热门做可比较的实验。

- [工程审查、真实实验与下一阶段建议](../docs/P0-REC-01-验收记录.md)
- [数据验收及样本限制](../docs/P0-REC-01-数据验收.md)
- [Benchmark 协议和指标公式](../docs/P0-REC-01-Benchmark.md)
- [数据验收 JSON](reports/p0-rec-01-audit.json) · [Benchmark JSON](reports/p0-rec-01-benchmark.json)

## 文件入口

| 文件 | 用途 |
| --- | --- |
| `sample.py` | 按哈希核对并读取 Parquet，审计字段、购买分布、抽样规则和网站五列导出。 |
| `benchmark.py` | 时间切分、训练历史、候选约束、标签、分组以及统一指标。 |
| `popular.py` | 从历史构建购买热度榜、每用户过滤、冷启动兜底和协议响应。 |
| `run.py` | 离线命令入口，生成聚合结果、排序指纹、版本和资源记录。 |
| `popular_v1.json`、`requirements.txt` | 固定样本指纹、时间窗口、模型版本、TopK 和依赖入口。 |
| `tests/test_benchmark.py` | 用人工可复算的输入检查边界、排序与指标。 |

## 三位用户、五件商品

假设训练期内，甲各购买 A、C 一次，乙各购买 A、B 一次。D 被浏览过，E 被加购过。丙在训练期无行为。

| 商品 | A | B | C | D | E |
| --- | ---: | ---: | ---: | ---: | ---: |
| 购买热度 | 2 | 1 | 1 | 0 | 0 |

B 和 C 的热度相同，按 ID 顺序排列，因此榜单为 A、B、C、D、E。若 K=3，甲从榜单过滤掉已买的 A、C，得到 B、D、E。乙过滤 A、B，得到 C、D、E。丙没有历史，直接从热门榜获得 A、B、C，来源标为热门兜底。

在真实测试窗口中，有 7 名用户恰好有两个可达购买目标且只在第 2 位命中一个。以这种匿名结果手算，Recall@10 为 1 ÷ 2 = 0.5；DCG 为 1 ÷ log₂(3) ≈ 0.63093。两个目标若理想排在第 1、2 位，IDCG 为 1 + 1 ÷ log₂(3) ≈ 1.63093，因此 NDCG@10 ≈ 0.38685。所有用户指标先独立计算，再按协议做宏平均。

## 在 Windows PowerShell 复跑

从仓库根目录执行。样本仍放在工作区的交接包中，可以把 `$sample` 改为其他本地绝对路径。

~~~powershell
py -3.12 -m venv .venv
$py = '.\.venv\Scripts\python.exe'
& $py -m pip install -r recommendation/requirements.txt
$sample = '..\交接包_张凯博_2026-11-20\data\processed\events_sample.parquet'

& $py -m pytest -q recommendation/tests
& $py -m recommendation.run audit --sample $sample
& $py -m recommendation.run benchmark --sample $sample
~~~

| 命令 | 意义与成功输出 |
| --- | --- |
| `py -3.12 -m venv .venv` | 建立推荐和数据组共用的本地虚拟环境。直接调用其 python.exe 即可。后端依赖安装在自己的 backend/.venv。 |
| `pip install -r recommendation/requirements.txt` | 安装沿用数据组锁定的 Parquet、数据分析与测试依赖。 |
| `pytest -q recommendation/tests` | 检查人工构造的边界案例。阶段一基线为 16 项，目前含 FIX 和 BPR 用例共 62 项。 |
| `recommendation.run audit` | 校验真实样本并生成 audit.json。终端打印 rows=1079354 users=52687 purchase_users=6976。 |
| `recommendation.run benchmark` | 构建验证和测试快照并输出 benchmark.json。验证与测试主口径分别有 2,069 和 1,122 个用户。测试 Recall@10 约为 0.17365，NDCG@10 约为 0.10100。 |

默认运行结果写入忽略目录 `artifacts/p0-rec-01/`。要检查确定性，可以换 Python 的哈希种子并写到另一个目录。

~~~powershell
$env:PYTHONHASHSEED = '91'
& $py -m recommendation.run benchmark --sample $sample --out-dir artifacts/p0-rec-01/repeat
Remove-Item Env:PYTHONHASHSEED
@'
import json
from pathlib import Path

output = Path("artifacts/p0-rec-01")
first = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))
repeat = json.loads((output / "repeat/benchmark.json").read_text(encoding="utf-8"))
assert first["result"] == repeat["result"]
print("benchmark results and ranking fingerprints match")
'@ | & $py -
~~~

后端现有适配使用 `user_id / dataset_id / model_version / limit`，核心的 `PopularModel.predict` 生成对应响应。线下使用 `local-parquet-sha256-…`。待取得真实导入版本和校验记录后，再为实际网站版本登记兼容模型及部署预加载推理服务。BPR 的本轮离线结果等待阶段审查。

详见 [后端交付约定](../docs/数据与算法交付约定.md) 与 [数据组说明](../data_algo/README.md)。
