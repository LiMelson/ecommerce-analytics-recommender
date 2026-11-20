import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from data_algo.cleaning import load_events
from data_algo.website_export import export_website

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.csv"
BACKEND_IMPORTING = (
    Path(__file__).resolve().parents[2] / "backend" / "app" / "importing.py"
)


def _unknown_category_csv(tmp_path: Path) -> Path:
    path = tmp_path / "unknown_category.csv"
    path.write_text(
        "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session\n"
        "2019-10-01 10:00:00 UTC,view,2001,,,x,10.0,u1,s1\n"
        "2019-10-01 10:01:00 UTC,remove_from_cart,2001,,,x,10.0,u1,s1\n"
        "2019-10-01 10:02:00 UTC,purchase,2001,,,x,10.0,u1,s1\n",
        encoding="utf-8",
    )
    return path


def test_export_website_csv_columns_timestamp_and_exclusions(tmp_path: Path) -> None:
    df = load_events(FIXTURE)
    output = tmp_path / "website.csv"
    report = export_website(
        df,
        output,
        source_path=FIXTURE,
        report_path=tmp_path / "report.json",
    )

    exported = pd.read_csv(output, dtype=str, encoding="utf-8-sig")
    assert list(exported.columns) == [
        "user_id",
        "item_id",
        "category_id",
        "behavior_type",
        "timestamp",
    ]
    assert len(exported) == 12
    assert set(exported["behavior_type"]) == {"pv", "cart", "buy"}
    assert report["excluded_by_type"] == {"remove_from_cart": 1}
    assert report["timestamp_unit"] == "milliseconds since epoch UTC"
    assert int(exported.iloc[0]["timestamp"]) == 1569924000000
    assert (tmp_path / "report.json").exists()


def test_export_website_unknown_category_is_empty(tmp_path: Path) -> None:
    source = _unknown_category_csv(tmp_path)
    df = load_events(source)
    output = tmp_path / "website.csv"
    report = export_website(df, output, source_path=source)

    exported = pd.read_csv(output, dtype=str, encoding="utf-8-sig")
    assert len(exported) == 2
    assert exported["category_id"].isna().all()
    assert report["unknown_category_rows"] == 2
    assert report["excluded_rows"] == 1


@pytest.mark.skipif(
    not BACKEND_IMPORTING.exists(),
    reason="后端 importing.py 不在当前工作区（单独运行 data_algo 时跳过）",
)
@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_export_website_is_compatible_with_backend_importing(
    tmp_path: Path, suffix: str
) -> None:
    source = _unknown_category_csv(tmp_path)
    df = load_events(source)
    output = tmp_path / f"website{suffix}"
    export_website(df, output, source_path=source)

    spec = importlib.util.spec_from_file_location(
        "backend_importing_for_test", BACKEND_IMPORTING
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = list(module.records(output, 100))
    assert len(rows) == 2
    assert rows[0][1][3] in {"pv", "fav", "cart", "buy"}
    assert rows[0][1][4] == 1569924000000


def test_export_website_rejects_output_equal_input(tmp_path: Path) -> None:
    source = _unknown_category_csv(tmp_path)
    before = source.read_bytes()
    df = load_events(source)
    with pytest.raises(ValueError, match="输入文件"):
        export_website(df, source, source_path=source)
    assert source.read_bytes() == before
