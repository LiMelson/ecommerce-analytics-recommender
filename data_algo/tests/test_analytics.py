from pathlib import Path

import pytest

from data_algo.analytics import (
    cart_abandonment,
    conversion_funnel,
    export_analytics,
    overview,
    profiles_table,
    rankings,
    retention,
    rfm_table,
    user_profile,
)
from data_algo.cleaning import load_events

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.csv"


@pytest.fixture(scope="module")
def events():
    return load_events(FIXTURE)


def test_overview_counts_and_amount(events) -> None:
    result = overview(events)
    assert result["total_events"] == 13
    assert result["users"] == 4
    assert result["items"] == 4
    assert result["categories"] == 4
    assert result["brands"] == 3
    assert result["behavior_counts"]["pv"] == 5
    assert result["behavior_counts"]["cart"] == 3
    assert result["behavior_counts"]["buy"] == 4
    assert result["behavior_counts"]["remove_from_cart"] == 1
    assert result["purchase_amount"] == 225.0
    assert len(result["daily"]) == 4
    assert len(result["hourly"]) == 24
    assert sum(row["pv"] for row in result["daily"]) == 5
    assert sum(row["buy"] for row in result["daily"]) == 4


def test_daily_uses_beijing_time(events) -> None:
    result = overview(events)
    by_date = {row["date"]: row for row in result["daily"]}
    assert by_date["2019-10-01"]["pv"] == 2
    assert by_date["2019-10-01"]["cart"] == 2
    assert by_date["2019-10-01"]["buy"] == 2
    assert by_date["2019-10-02"]["remove_from_cart"] == 1
    assert by_date["2019-10-04"]["buy"] == 1


def test_funnel_requires_order(events) -> None:
    result = conversion_funnel(events)
    values = {stage["key"]: stage["value"] for stage in result["stages"]}
    assert values == {"pv": 5, "cart": 2, "buy": 1}
    assert result["conversion_rates"]["pv_to_cart"] == 0.4
    assert result["conversion_rates"]["cart_to_buy"] == 0.5
    assert result["conversion_rates"]["pv_to_buy"] == 0.2


def test_rfm_handles_non_buyers_and_monetary(events) -> None:
    rfm, summary = rfm_table(events)
    rows = {row["user_id"]: row for row in rfm.to_dict(orient="records")}

    assert rows["1"]["frequency"] == 2
    assert rows["1"]["recency_days"] == 2
    assert rows["1"]["monetary"] == 100.0
    assert rows["1"]["segment"] == "近期购买用户"

    assert rows["2"]["frequency"] == 1
    assert rows["2"]["recency_days"] == 3
    assert rows["2"]["monetary"] == 100.0

    assert rows["3"]["frequency"] == 1
    assert rows["3"]["recency_days"] == 0
    assert rows["3"]["monetary"] == 25.0

    assert rows["4"]["frequency"] == 0
    assert rows["4"]["recency_days"] is None
    assert rows["4"]["monetary"] is None
    assert rows["4"]["segment"] == "潜在转化用户"

    assert summary["users"] == 4
    assert summary["buyers"] == 3
    assert summary["non_buyers"] == 1


def test_retention_distinguishes_null_and_zero(events) -> None:
    result = retention(events)
    cohorts = {row["day"]: row for row in result["cohorts"]}

    assert cohorts["2019-10-01"]["users"] == 2
    assert cohorts["2019-10-01"]["retention"][0] == 1.0
    assert cohorts["2019-10-01"]["retention"][1] == 0.5
    assert cohorts["2019-10-01"]["retention"][2] == 0.0
    assert cohorts["2019-10-01"]["retention"][3] == 0.0
    assert cohorts["2019-10-01"]["retention"][4] is None

    assert cohorts["2019-10-02"]["retention"][1] == 0.0
    assert cohorts["2019-10-02"]["retention"][2] == 1.0
    assert cohorts["2019-10-02"]["retention"][3] is None
    assert result["day1_average"] == 0.25
    assert result["day7_average"] is None


def test_rankings_by_category_brand_item(events) -> None:
    result = rankings(events)
    assert result["buy_events"] == 4
    assert result["amount"] == 225.0

    categories = result["categories"]
    assert categories[0]["category_id"] == "2001"
    assert categories[0]["count"] == 2
    assert categories[0]["amount"] == 200.0
    assert categories[1]["category_id"] == "2003"
    assert categories[2]["category_id"] == "2004"

    brands = result["brands"]
    assert brands[0]["brand"] == "xiaomi"
    assert brands[0]["count"] == 2
    assert brands[1]["brand"] == "unknown"

    items = result["items"]
    assert items[0]["item_id"] == "1001"
    assert items[0]["count"] == 2
    assert items[1]["item_id"] == "1003"
    assert items[2]["item_id"] == "1004"


def test_cart_abandonment(events) -> None:
    result = cart_abandonment(events)
    assert result is not None
    assert result["sessions"]["cart_sessions"] == 3
    assert result["sessions"]["converted_sessions"] == 2
    assert result["sessions"]["abandoned_sessions"] == 1
    assert result["sessions"]["abandonment_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert result["user_item_pairs"]["cart_pairs"] == 3
    assert result["user_item_pairs"]["abandoned_pairs"] == 1


def test_profiles_and_single_user_profile(events) -> None:
    rfm, _ = rfm_table(events)
    profiles, summary = profiles_table(events, rfm=rfm)
    rows = {row["user_id"]: row for row in profiles.to_dict(orient="records")}

    assert rows["1"]["event_count"] == 4
    assert rows["1"]["top_category_id"] == "2001"
    assert rows["1"]["top_brand"] == "xiaomi"
    assert rows["1"]["frequency"] == 2

    assert rows["4"]["event_count"] == 2
    assert rows["4"]["frequency"] == 0
    assert rows["4"]["segment"] == "潜在转化用户"
    assert rows["4"]["top_category_id"] in {"2001", "2002"}

    assert summary["users"] == 4

    profile = user_profile(events, "1", rfm=rfm)
    assert profile is not None
    assert profile["top_categories"][0]["category_id"] == "2001"
    assert profile["top_brands"][0]["brand"] == "xiaomi"
    assert len(profile["recent_events"]) == 4
    assert profile["rfm"]["recency_days"] == 2

    assert user_profile(events, "999", rfm=rfm) is None


def test_export_analytics_writes_files(events, tmp_path: Path) -> None:
    result = export_analytics(events, tmp_path)
    assert result["overview"]["total_events"] == 13
    for name in [
        "overview.json",
        "daily.json",
        "hourly.json",
        "funnel.json",
        "rfm_summary.json",
        "profiles_summary.json",
        "retention.json",
        "rankings.json",
        "meta.json",
        "rfm.parquet",
        "profiles.parquet",
        "profiles_sample.json",
    ]:
        assert (tmp_path / name).exists(), name
