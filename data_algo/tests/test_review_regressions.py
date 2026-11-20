from pathlib import Path

import pytest

from data_algo.cleaning import (
    build_sample,
    load_events,
    render_quality_markdown,
)
from data_algo.duckdb_pipeline import build_sample_duckdb
from data_algo import path_utils
from data_algo.path_utils import atomic_output

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.csv"


def _write_required_only(path: Path) -> None:
    path.write_text(
        "event_time,event_type,product_id,user_id\n"
        "2019-10-01 10:00:00 UTC,view,1001,1\n"
        "2019-10-01 10:01:00 UTC,cart,1001,1\n"
        "2019-10-01 10:02:00 UTC,purchase,1001,1\n",
        encoding="utf-8",
    )


def _write_empty_price(path: Path) -> None:
    path.write_text(
        "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session\n"
        "2019-10-01 10:00:00 UTC,view,1001,2001,electronics.smartphone,xiaomi,,1,s1\n"
        "2019-10-01 10:01:00 UTC,cart,1001,2001,electronics.smartphone,xiaomi,,1,s1\n"
        "2019-10-01 10:02:00 UTC,purchase,1001,2001,electronics.smartphone,xiaomi,,1,s1\n",
        encoding="utf-8",
    )


def test_atomic_output_keeps_old_file_on_error(tmp_path: Path) -> None:
    final = tmp_path / "result.txt"
    final.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_output(final) as temp:
            temp.write_text("new", encoding="utf-8")
            raise RuntimeError("boom")
    assert final.read_text(encoding="utf-8") == "old"


def test_build_sample_rejects_output_equal_input(tmp_path: Path) -> None:
    before = FIXTURE.read_bytes()
    with pytest.raises(ValueError, match="输入文件"):
        build_sample(
            [FIXTURE],
            FIXTURE,
            quality_json=tmp_path / "q.json",
            quality_md=tmp_path / "q.md",
            sample_meta=tmp_path / "m.json",
            per_mille=500,
            progress=False,
        )
    assert FIXTURE.read_bytes() == before


def test_build_sample_rejects_report_equal_input(tmp_path: Path) -> None:
    before = FIXTURE.read_bytes()
    with pytest.raises(ValueError, match="输入文件"):
        build_sample(
            [FIXTURE],
            tmp_path / "sample.parquet",
            quality_json=FIXTURE,
            per_mille=500,
            progress=False,
        )
    assert FIXTURE.read_bytes() == before


def test_build_sample_duckdb_rejects_output_equal_input(tmp_path: Path) -> None:
    before = FIXTURE.read_bytes()
    with pytest.raises(ValueError, match="输入文件"):
        build_sample_duckdb(
            [FIXTURE],
            FIXTURE,
            quality_json=tmp_path / "q.json",
            quality_md=tmp_path / "q.md",
            sample_meta=tmp_path / "m.json",
            per_mille=500,
            threads=2,
            progress=False,
        )
    assert FIXTURE.read_bytes() == before


def test_build_sample_preserves_existing_output_on_writer_failure(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "sample.parquet"
    output.write_bytes(b"OLD")

    class Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("writer failed")

    monkeypatch.setattr("data_algo.cleaning.pq.ParquetWriter", Boom)
    with pytest.raises(RuntimeError):
        build_sample(
            [FIXTURE],
            output,
            per_mille=500,
            chunksize=100,
            progress=False,
        )
    assert output.read_bytes() == b"OLD"
    assert not list(tmp_path.glob(".sample.parquet.*"))


def test_duckdb_preserves_existing_output_on_validation_failure(tmp_path: Path) -> None:
    output = tmp_path / "sample.parquet"
    output.write_bytes(b"OLD")
    with pytest.raises(ValueError):
        build_sample_duckdb([FIXTURE], output, per_mille=0)
    assert output.read_bytes() == b"OLD"


def test_duckdb_cleans_temporary_output_on_replace_failure(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "sample.parquet"
    output.write_bytes(b"OLD")

    def fail_replace(source, destination):
        assert source.is_file()
        assert destination == output
        raise PermissionError("output is locked")

    monkeypatch.setattr(path_utils.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="output is locked"):
        build_sample_duckdb([FIXTURE], output, progress=False)

    assert output.read_bytes() == b"OLD"
    assert not list(tmp_path.glob(".sample.parquet.*"))


def test_duckdb_supports_required_only_columns(tmp_path: Path) -> None:
    source = tmp_path / "required_only.csv"
    _write_required_only(source)
    output = tmp_path / "sample.parquet"
    result = build_sample_duckdb(
        [source],
        output,
        quality_json=tmp_path / "q.json",
        quality_md=tmp_path / "q.md",
        sample_meta=tmp_path / "m.json",
        per_mille=1000,
        threads=2,
        progress=False,
    )
    assert result["sample"]["rows"] == 3
    df = load_events(output)
    assert df["event_ts"].iloc[0] == 1569924000000000000
    assert set(df["category_id"]) == {"unknown"}
    assert df["category_code"].isna().all()
    assert df["brand"].isna().all()
    assert df["session_id"].isna().all()
    assert df["price"].isna().all()
    assert result["quality"]["missing_counts"]["price"] == 3


def test_duckdb_supports_multiple_files_with_different_optional_columns(
    tmp_path: Path,
) -> None:
    required = tmp_path / "required.csv"
    _write_required_only(required)
    full = tmp_path / "second" / required.name
    full.parent.mkdir()
    full.write_text(
        "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session\n"
        "2019-10-02 10:00:00 UTC,view,1002,2002,electronics.audio.headphone,sony,99.0,2,s2\n"
        "2019-10-02 10:01:00 UTC,purchase,1002,2002,electronics.audio.headphone,sony,99.0,2,s2\n",
        encoding="utf-8",
    )
    output = tmp_path / "sample.parquet"
    result = build_sample_duckdb(
        [required, full],
        output,
        quality_json=tmp_path / "q.json",
        quality_md=tmp_path / "q.md",
        sample_meta=tmp_path / "m.json",
        per_mille=1000,
        threads=2,
        progress=False,
    )
    assert result["sample"]["rows"] == 5
    assert [entry["rows"] for entry in result["quality"]["inputs"]] == [3, 2]
    df = load_events(output)
    assert len(df) == 5
    assert df["price"].notna().sum() == 2


def test_quality_markdown_handles_zero_price_count() -> None:
    report = {
        "generated_at": "2026-11-19T00:00:00Z",
        "inputs": [],
        "rows_scanned": 0,
        "valid_rows": 0,
        "invalid_rows": 0,
        "duplicate_rows": 0,
        "duplicate_scope": "sample_deduped",
        "kept_rows": 0,
        "behavior_type_counts": {},
        "raw_event_type_counts": {},
        "invalid_reasons": {},
        "missing_counts": {"category_code": 0, "brand": 0, "price": 0},
        "price": {"count": 0, "sum": 0, "min": None, "max": None, "zero": 0, "negative": 0},
        "time_range": {"min": None, "max": None},
        "unique": {},
        "invalid_examples": [],
        "sample": {"per_mille": 10},
        "notes": [],
    }
    text = render_quality_markdown(report)
    assert "均值 —" in text
    assert "— 至 —" in text


def test_pandas_handles_empty_and_all_invalid_inputs(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text(
        "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session\n",
        encoding="utf-8",
    )
    empty_output = tmp_path / "empty.parquet"
    empty_result = build_sample(
        [empty],
        empty_output,
        quality_json=tmp_path / "empty.json",
        quality_md=tmp_path / "empty.md",
        sample_meta=tmp_path / "empty_meta.json",
        per_mille=10,
        chunksize=100,
        progress=False,
    )
    assert empty_result["quality"]["rows_scanned"] == 0
    assert (tmp_path / "empty.md").exists()

    invalid = tmp_path / "invalid.csv"
    invalid.write_text(
        "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session\n"
        "2019-10-01 10:00:00 UTC,unknown,1001,2001,,,10.0,1,s1\n",
        encoding="utf-8",
    )
    invalid_result = build_sample(
        [invalid],
        tmp_path / "invalid.parquet",
        quality_json=tmp_path / "invalid.json",
        quality_md=tmp_path / "invalid.md",
        sample_meta=tmp_path / "invalid_meta.json",
        per_mille=10,
        chunksize=100,
        progress=False,
    )
    assert invalid_result["quality"]["invalid_rows"] == 1
    assert invalid_result["quality"]["kept_rows"] == 0
    assert (tmp_path / "invalid.md").exists()


def test_duckdb_quality_report_handles_all_prices_missing(tmp_path: Path) -> None:
    source = tmp_path / "empty_price.csv"
    _write_empty_price(source)
    result = build_sample_duckdb(
        [source],
        tmp_path / "sample.parquet",
        quality_json=tmp_path / "q.json",
        quality_md=tmp_path / "q.md",
        sample_meta=tmp_path / "m.json",
        per_mille=1000,
        threads=2,
        progress=False,
    )
    assert result["quality"]["price"]["count"] == 0
    assert "均值 —" in (tmp_path / "q.md").read_text(encoding="utf-8")
