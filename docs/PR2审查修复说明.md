# PR #2 审查修复说明

修复日期：2026-11-19  
对应审查：PR #2 审查报告（组长提供，未入库）  
PR：<https://github.com/LiMelson/ecommerce-analytics-recommender/pull/2>

已把分支 rebase 到最新主分支（含 FastAPI/MySQL/ClickHouse 后端），
逐条处理审查报告的 P1/P2 问题，并补充回归测试与 `data_algo` CI。

## 1. [P1] 输入/输出路径保护与原子写入

- 新增 `data_algo/path_utils.py`：
  - `ensure_distinct_paths()` 在 mkdir/unlink/writer 之前检查输入、输出、报告路径；
  - 比较 `resolve()` 后的规范路径，并用 `os.path.samefile` 处理同一文件别名/硬链接；
  - 输出与任一输入相同、报告与输入相同、输出之间相同都会直接拒绝。
- 两个引擎都先写同目录临时 Parquet，成功关闭后再 `os.replace` 替换最终文件；
  - 失败时保留上一次可用输出；
  - 质量报告/元信息 JSON 和 Markdown 也改为临时文件 + 原子替换。
- 回归测试：
  - `test_build_sample_rejects_output_equal_input`
  - `test_build_sample_rejects_report_equal_input`
  - `test_build_sample_duckdb_rejects_output_equal_input`
  - `test_atomic_output_keeps_old_file_on_error`
  - `test_build_sample_preserves_existing_output_on_writer_failure`
  - `test_duckdb_preserves_existing_output_on_validation_failure`

## 2. [P2] 同时间戳严格转化

- `conversion_funnel` 改为按时间戳分组的状态机：
  - 要求 `view_time < cart_time < buy_time` 严格递增；
  - 同一时间戳最多推进一个阶段，不再依赖文件行序或 `event_ts` 推断先后；
  - 后续存在明确先后顺序的有效路径仍会保留。
- 新增 `test_funnel_same_timestamp_is_order_independent`：
  - 同一秒浏览/加购/购买只算到浏览；
  - 同一秒加购+购买后，下一分钟再加购、下一分钟再购买可走完整路径；
  - 正序/逆序输入结果一致。
- 用 1% 真实样本重算：555,180 → 20,556（3.70%）→ 8,470（41.20%）。

## 3. [P2] DuckDB 可选列缺失

- `_create_views` 改为按每个文件的实际表头生成 `SELECT`：
  - 缺失的 `category_id/category_code/brand/price/user_session` 补 `NULL::VARCHAR`；
  - 多个文件可选字段不同也能 `UNION ALL`；
  - 不再出现表头校验通过、默认 DuckDB 建视图却 BinderException。
- 回归测试：
  - `test_duckdb_supports_required_only_columns`
  - `test_duckdb_supports_multiple_files_with_different_optional_columns`

## 4. [P2] 全价格缺失除零

- `render_quality_markdown` 在 `price.count == 0` 时显示均值“—”，不再除法；
- 价格范围缺失显示“— 至 —”；
- 回归测试：
  - `test_quality_markdown_handles_zero_price_count`
  - `test_pandas_handles_empty_and_all_invalid_inputs`
  - `test_duckdb_quality_report_handles_all_prices_missing`

## 5. [P2] 网站标准入库导出

- 新增 `data_algo/website_export.py` 和 CLI 命令 `export-website`：
  - 只输出 `user_id,item_id,category_id,behavior_type,timestamp` 五列；
  - timestamp 为 UTC 毫秒（13 位）；
  - 只保留 pv/fav/cart/buy；remove_from_cart 等排除并写入报告；
  - 未知类目导出为空；
  - 输出 CSV 或 Parquet（按后缀）；
  - 导出报告记录输入/导出/排除行数、排除类型、未知类目行数和时间戳范围。
- 与主分支 `backend/app/importing.py` 的实际校验器做兼容测试：
  - CSV 和 Parquet 两种格式都通过；
  - 测试在生产代码中直接加载 `backend/app/importing.py`，不复制校验逻辑。
- 完整训练 Parquet 保留品牌、价格、会话等特征；网站五列文件不替代训练文件。

## 6. 合并与文档

- 分支已 rebase 到最新主分支 `7a6d85a`，保留后端启动说明、数据库/接口约定；
- 根 README 增加 `data_algo/` 目录和“数据清洗与离线分析”状态；
- `data/README.md` 更新主数据集 220316、`exports/` 目录和五列标准导出；
- `recommendation/README.md` 增加完整训练 Parquet 与网站五列文件的区分；
- 删除“组长后续搭建 FastAPI”“前端仍用演示统计、替换 JSON 即可”等过时描述；
- 离线 JSON 明确定位为核对口径/报告，不替代网站已有统计接口。

## 7. CI

新增 `.github/workflows/data-algo.yml`，在 PR/push 变更 `data_algo/**`、
`backend/app/importing.py`、`docs/数据与算法交付约定.md` 时：
- Python 3.12；
- 安装 `data_algo/requirements.txt`；
- 运行 `pytest -q`（含后端导入兼容测试）。

## 验证

在包含主分支后端的工作区执行：

```bash
cd data_algo
python -m pytest -q
```

结果：**30 passed**（本地无 backend 时 28 passed + 2 skipped）。

## 未提交内容

- 原始 2019-Oct/Nov CSV；
- 1% 抽样 Parquet（约 20MB）；
- 用户级画像/Parquet；
- 网站导出文件（本地生成，约几十 MB）。

原因：体积和数据许可。审查复现所需的合成夹具、代码、测试和聚合快照均已提交。
