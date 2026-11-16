"""命令行入口。

常用命令（在 data_algo/ 目录下执行）：

    .venv/Scripts/python -m data_algo.cli build-sample
    .venv/Scripts/python -m data_algo.cli analytics
    .venv/Scripts/python -m data_algo.cli profile --user-id 123
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analytics import export_analytics, user_profile
from .cleaning import build_sample, load_events
from .io_utils import to_native


DEFAULT_INPUT_PAIRS = [
    ("../data/raw/2019-Oct.csv", "../data/raw/2019-Nov.csv"),
    ("../archive/2019-Oct.csv", "../archive/2019-Nov.csv"),
]


def _default_inputs() -> list[str]:
    """按团队仓库约定的 data/raw 优先，兼容本地独立的 archive 目录。"""
    for pair in DEFAULT_INPUT_PAIRS:
        candidates = [Path(p) for p in pair]
        if all(p.exists() for p in candidates):
            return [str(p) for p in candidates]
    return []


def cmd_build_sample(args: argparse.Namespace) -> None:
    inputs = args.inputs or _default_inputs()
    if not inputs:
        raise SystemExit(
            "找不到默认输入文件，请用 --inputs 指定 CSV，例如：\n"
            "  --inputs ../archive/2019-Oct.csv ../archive/2019-Nov.csv"
        )

    engine = args.engine
    if engine == "duckdb":
        try:
            from .duckdb_pipeline import build_sample_duckdb
        except ImportError:
            print("未安装 duckdb，回退到 pandas 逐块扫描（会慢很多）")
            engine = "pandas"

    common = dict(
        quality_json=args.quality_json,
        quality_md=args.quality_md,
        sample_meta=args.sample_meta,
        per_mille=args.per_mille,
        seed=args.seed,
        max_examples=args.max_examples,
    )
    if engine == "duckdb":
        result = build_sample_duckdb(
            inputs,
            args.output,
            threads=args.threads,
            memory_limit=args.memory_limit,
            **common,
        )
    else:
        result = build_sample(
            inputs,
            args.output,
            chunksize=args.chunksize,
            **common,
        )

    quality = result["quality"]
    sample = result["sample"]
    print("\n=== 质量报告 ===")
    print(f"引擎: {quality.get('engine', 'pandas')}")
    print(f"扫描行数: {quality['rows_scanned']:,}")
    print(f"有效行: {quality['valid_rows']:,}")
    print(f"无效行: {quality['invalid_rows']:,}")
    print(f"重复行: {quality['duplicate_rows']:,}")
    print(f"行为分布: {quality['behavior_type_counts']}")
    print(f"时间范围(UTC): {quality['time_range']}")
    print("\n=== 抽样结果 ===")
    print(f"用户数: {sample['users']:,}")
    print(f"行数: {sample['rows']:,}")
    print(f"输出: {sample['output']}")
    if args.quality_md:
        print(f"质量报告: {args.quality_md}")


def cmd_analytics(args: argparse.Namespace) -> None:
    df = load_events(args.input)
    sample_meta = None
    if args.sample_meta and Path(args.sample_meta).exists():
        sample_meta = json.loads(Path(args.sample_meta).read_text(encoding="utf-8"))
    result = export_analytics(
        df,
        args.outdir,
        sample_meta=sample_meta,
        half_life_days=args.half_life_days,
    )
    overview = result["overview"]
    print("\n=== 分析完成 ===")
    print(f"事件数: {overview['total_events']:,}")
    print(f"用户数: {overview['users']:,}")
    print(f"商品数: {overview['items']:,}")
    print(f"类目数: {overview['categories']:,}")
    print(f"行为分布: {overview['behavior_counts']}")
    print(f"购买金额(行为口径): {overview['purchase_amount']}")
    print(f"漏斗: {[(s['name'], s['value']) for s in result['funnel']['stages']]}")
    print(f"输出目录: {args.outdir}")


def cmd_profile(args: argparse.Namespace) -> None:
    df = load_events(args.input)
    result = user_profile(df, args.user_id, top_n=args.top_n)
    if result is None:
        raise SystemExit(f"用户 {args.user_id} 不在当前抽样中")
    print(json.dumps(to_native(result), ensure_ascii=False, indent=2))


def cmd_info(args: argparse.Namespace) -> None:
    df = load_events(args.input)
    print(
        json.dumps(
            to_native(
                {
                    "events": len(df),
                    "users": int(df["user_id"].nunique()),
                    "items": int(df["item_id"].nunique()),
                    "time_min_utc": df["event_time"].min().isoformat(),
                    "time_max_utc": df["event_time"].max().isoformat(),
                    "behavior_counts": {
                        str(k): int(v)
                        for k, v in df["behavior_type"].value_counts().items()
                    },
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="电商用户行为数据清洗、抽样与统计分析"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build-sample", help="扫描原始 CSV，生成质量报告与按用户抽样")
    p.add_argument("--inputs", nargs="*", help="原始 CSV 文件列表")
    p.add_argument(
        "--output",
        default="../data/processed/events_sample.parquet",
        help="抽样 Parquet 输出路径（团队仓库约定 data/processed/）",
    )
    p.add_argument("--quality-json", default="outputs/quality_report.json")
    p.add_argument("--quality-md", default="outputs/quality_report.md")
    p.add_argument("--sample-meta", default="outputs/sample_meta.json")
    p.add_argument("--per-mille", type=int, default=10, help="千分比，10 表示 1%% 用户")
    p.add_argument("--seed", type=int, default=20261116)
    p.add_argument(
        "--engine",
        choices=["duckdb", "pandas"],
        default="duckdb",
        help="duckdb 适合大文件（默认）；pandas 适合小样本/没有 duckdb 时",
    )
    p.add_argument("--threads", type=int, default=4, help="DuckDB 线程数")
    p.add_argument("--memory-limit", default="4GB", help="DuckDB 内存上限")
    p.add_argument("--chunksize", type=int, default=300_000, help="pandas 引擎的块大小")
    p.add_argument("--max-examples", type=int, default=50)
    p.set_defaults(func=cmd_build_sample)

    p = sub.add_parser("analytics", help="对抽样数据计算全部统计并导出 JSON")
    p.add_argument(
        "--input",
        default="../data/processed/events_sample.parquet",
        help="抽样 Parquet / CSV（团队仓库约定 data/processed/）",
    )
    p.add_argument("--outdir", default="outputs/analytics")
    p.add_argument("--sample-meta", default="outputs/sample_meta.json")
    p.add_argument("--half-life-days", type=float, default=3.0)
    p.set_defaults(func=cmd_analytics)

    p = sub.add_parser("profile", help="查询单个用户的画像")
    p.add_argument("--input", default="../data/processed/events_sample.parquet")
    p.add_argument("--user-id", required=True)
    p.add_argument("--top-n", type=int, default=5)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("info", help="查看抽样数据概况")
    p.add_argument("--input", default="../data/processed/events_sample.parquet")
    p.set_defaults(func=cmd_info)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
