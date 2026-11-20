from pathlib import Path

import pandas as pd

from data_algo.cleaning import build_sample, load_events, normalize_chunk, sample_user_mask

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.csv"


def read_fixture() -> pd.DataFrame:
    return pd.read_csv(
        FIXTURE,
        dtype="string",
        keep_default_na=False,
        encoding="utf-8-sig",
    )


def test_normalize_chunk_maps_behaviors_and_reports_invalid() -> None:
    out, stats, examples = normalize_chunk(read_fixture(), max_examples=10)

    assert stats["rows"] == 18
    assert stats["valid_before_dedupe"] == 14
    assert stats["invalid"] == 4
    assert stats["duplicates"] == 1
    assert stats["kept"] == 13
    assert stats["behavior_counts"] == {
        "pv": 5,
        "buy": 4,
        "cart": 3,
        "remove_from_cart": 1,
    }
    assert stats["invalid_reasons"] == {
        "missing_user": 1,
        "missing_item": 0,
        "missing_time": 0,
        "invalid_time": 1,
        "unknown_behavior": 1,
        "negative_price": 1,
    }
    assert stats["zero_price"] == 1
    assert stats["missing_category_code"] == 1
    assert stats["missing_brand"] == 1
    assert len(examples) == 4
    assert out["event_time"].dt.tz is not None
    assert str(out["event_time"].dt.tz) == "UTC"


def test_sample_user_mask_is_stable_and_full_history() -> None:
    user_ids = pd.Series(["1", "2", "3", "4", "5"], dtype="string")
    first = sample_user_mask(user_ids, 5, 20261116)
    second = sample_user_mask(user_ids, 5, 20261116)
    assert first.tolist() == second.tolist()
    assert first.tolist() == [True, True, True, True, False]


def test_build_sample_writes_parquet_and_quality_report(tmp_path: Path) -> None:
    output = tmp_path / "sample.parquet"
    result = build_sample(
        [FIXTURE],
        output,
        quality_json=tmp_path / "quality.json",
        quality_md=tmp_path / "quality.md",
        sample_meta=tmp_path / "meta.json",
        per_mille=500,
        chunksize=100,
        max_examples=10,
        progress=False,
    )

    assert result["quality"]["rows_scanned"] == 18
    assert result["quality"]["invalid_rows"] == 4
    assert result["quality"]["duplicate_rows"] == 1
    assert result["sample"]["users"] == 4
    assert result["sample"]["rows"] == 13
    assert output.exists()
    assert (tmp_path / "quality.json").exists()
    assert (tmp_path / "quality.md").exists()
    assert (tmp_path / "meta.json").exists()

    loaded = load_events(output)
    assert len(loaded) == 13
    # 被抽中的用户保留完整历史（用户 1 有 4 条去重后的行为）
    assert len(loaded[loaded["user_id"] == "1"]) == 4


def test_load_events_adds_beijing_time() -> None:
    df = load_events(FIXTURE)
    assert len(df) == 13
    first = df[df["user_id"] == "1"].iloc[0]
    assert first["date"] == "2019-10-01"
    assert int(first["hour"]) == 18
    assert int(first["event_ts"]) > 0
