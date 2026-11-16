-- Reference only; run python -m app.cli init for initial setup.

CREATE TABLE IF NOT EXISTS behavior_events (
      dataset_id String, sequence UInt64, user_id String, item_id String, category_id String,
      behavior_type LowCardinality(String), event_time_ms UInt64,
      event_time DateTime64(3,'UTC') MATERIALIZED fromUnixTimestamp64Milli(toInt64(event_time_ms)),
      event_date Date MATERIALIZED toDate(toTimeZone(event_time,'Asia/Shanghai')),
      INDEX item_filter item_id TYPE bloom_filter(0.01) GRANULARITY 4
    ) ENGINE=MergeTree PARTITION BY toYYYYMM(event_date)
      ORDER BY (dataset_id,event_date,user_id,event_time_ms,sequence)
      SETTINGS index_granularity=8192;

CREATE TABLE IF NOT EXISTS daily_stats (
      dataset_id String, day Date, pv UInt64, fav UInt64, cart UInt64, buy UInt64, users UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,day);

CREATE TABLE IF NOT EXISTS hourly_stats (
      dataset_id String, hour UInt8, pv UInt64, fav UInt64, cart UInt64, buy UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,hour);

CREATE TABLE IF NOT EXISTS user_stats (
      dataset_id String, user_id String, total UInt64, items UInt64,
      pv UInt64, fav UInt64, cart UInt64, buy UInt64, last_ms UInt64, last_buy_ms UInt64,
      first_day Date, active_days Array(Date)
    ) ENGINE=MergeTree ORDER BY (dataset_id,user_id);

CREATE TABLE IF NOT EXISTS purchase_rankings (
      dataset_id String, kind LowCardinality(String), id String, category String,
      count UInt64, users UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,kind,count,id);
