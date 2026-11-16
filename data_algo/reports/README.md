# 聚合结果快照

本目录保存**已跑通的真实数据聚合结果**，供前端联调、报告和验收参考。

- 数据：天池 220316 · 2019-Oct.csv + 2019-Nov.csv
- 抽样：1% 用户（52,687 人 / 1,079,354 条行为），保留完整历史
- 生成：2026-11-16，DuckDB 全量扫描 + Python 统计分析
- 许可：数据页标注 CC BY-ND 4.0；本目录只放聚合统计，**不放原始数据、不放用户级画像**

## 文件

| 文件 | 内容 |
| --- | --- |
| `quality_report.md` / `.json` | 全量数据质量报告（行数、行为分布、缺失、价格、唯一值） |
| `sample_meta.json` | 抽样参数、行数、用户数、时间范围 |
| `analytics/overview.json` | 概览 + 每日 + 每时 |
| `analytics/daily.json` | 每日趋势 |
| `analytics/hourly.json` | 每时趋势 |
| `analytics/funnel.json` | 顺序漏斗 |
| `analytics/rfm_summary.json` | RFM 分群汇总 |
| `analytics/retention.json` | 留存矩阵 |
| `analytics/rankings.json` | 购买类目/品牌/商品排行 |
| `analytics/cart_abandonment.json` | 购物车放弃 |
| `analytics/meta.json` | 数据版本、口径、限制 |

## 没有提交的内容

- `2019-Oct.csv`、`2019-Nov.csv`（13.7GB，原始数据）；
- `events_sample.parquet`（约 20MB，按 1% 用户抽样）；
- `profiles_sample.json`、`profiles.parquet`、`rfm.parquet`（用户级明细）。

原因：体积和许可边界。需要抽样 Parquet 或用户级画像时，按 [README](../README.md) 运行 `build-sample` 重新生成，或向数据负责人索取。

## 重新生成

```bash
cd data_algo
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m data_algo.cli build-sample --engine duckdb --per-mille 10
.venv/Scripts/python -m data_algo.cli analytics
```

原始 CSV 放在 `data/raw/`（团队仓库约定）或 `archive/`（本机独立开发）。
