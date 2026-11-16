"""ClickHouse persistence and SQL analytics. No model training or data cleaning."""

from contextlib import contextmanager
from datetime import timedelta

import clickhouse_connect

from .common import ms_day

RAW_COLUMNS = ["dataset_id", "sequence", "user_id", "item_id", "category_id", "behavior_type", "event_time_ms"]
RF = """multiIf(buy=0, '潜在转化用户',
    intDiv({reference:UInt64}-last_buy_ms,86400000)<=2 AND buy>=12, '高频活跃用户',
    intDiv({reference:UInt64}-last_buy_ms,86400000)<=2, '近期购买用户',
    buy>=12, '高频待唤回用户', '低频待唤回用户')"""

DDL = [
    """CREATE TABLE IF NOT EXISTS behavior_events (
      dataset_id String, sequence UInt64, user_id String, item_id String, category_id String,
      behavior_type LowCardinality(String), event_time_ms UInt64,
      event_time DateTime64(3,'UTC') MATERIALIZED fromUnixTimestamp64Milli(toInt64(event_time_ms)),
      event_date Date MATERIALIZED toDate(toTimeZone(event_time,'Asia/Shanghai')),
      INDEX item_filter item_id TYPE bloom_filter(0.01) GRANULARITY 4
    ) ENGINE=MergeTree PARTITION BY toYYYYMM(event_date)
      ORDER BY (dataset_id,event_date,user_id,event_time_ms,sequence)
      SETTINGS index_granularity=8192""",
    """CREATE TABLE IF NOT EXISTS daily_stats (
      dataset_id String, day Date, pv UInt64, fav UInt64, cart UInt64, buy UInt64, users UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,day)""",
    """CREATE TABLE IF NOT EXISTS hourly_stats (
      dataset_id String, hour UInt8, pv UInt64, fav UInt64, cart UInt64, buy UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,hour)""",
    """CREATE TABLE IF NOT EXISTS user_stats (
      dataset_id String, user_id String, total UInt64, items UInt64,
      pv UInt64, fav UInt64, cart UInt64, buy UInt64, last_ms UInt64, last_buy_ms UInt64,
      first_day Date, active_days Array(Date)
    ) ENGINE=MergeTree ORDER BY (dataset_id,user_id)""",
    """CREATE TABLE IF NOT EXISTS purchase_rankings (
      dataset_id String, kind LowCardinality(String), id String, category String,
      count UInt64, users UInt64
    ) ENGINE=MergeTree ORDER BY (dataset_id,kind,count,id)""",
]


class Analytics:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def client(self, database=None):
        s = self.settings
        client = clickhouse_connect.get_client(
            host=s.clickhouse_host,
            port=s.clickhouse_port,
            username=s.clickhouse_user,
            password=s.clickhouse_password.get_secret_value(),
            database=database or s.clickhouse_database,
            connect_timeout=5,
            send_receive_timeout=s.query_timeout_seconds + 15,
            autogenerate_session_id=False,
            settings={
                "max_execution_time": s.query_timeout_seconds,
                "max_threads": 2,
                "max_memory_usage": 1200000000,
                "max_bytes_before_external_group_by": 256000000,
                "max_bytes_before_external_sort": 128000000,
            },
        )
        try:
            yield client
        finally:
            client.close()

    def init_schema(self):
        with self.client("default") as client:
            client.command(f"CREATE DATABASE IF NOT EXISTS `{self.settings.clickhouse_database}`")
        with self.client() as client:
            for sql in DDL:
                client.command(sql)

    def ping(self):
        return self.query("SELECT 1 AS ok")[0]["ok"] == 1

    def query(self, sql, params=None):
        with self.client() as client:
            return list(client.query(sql, parameters=params or {}).named_results())

    def insert(self, records):
        with self.client() as client:
            # A failed batch makes the entire unpublished dataset fail. It is never retried in place.
            client.insert("behavior_events", records, column_names=RAW_COLUMNS)

    def summarize(self, dataset_id, heartbeat=lambda: None):
        p = {"dataset": dataset_id}
        counts = "countIf(behavior_type='pv'),countIf(behavior_type='fav'),countIf(behavior_type='cart'),countIf(behavior_type='buy')"
        statements = [
            f"""INSERT INTO daily_stats SELECT dataset_id,event_date,{counts},uniqExact(user_id)
                FROM behavior_events WHERE dataset_id={{dataset:String}} GROUP BY dataset_id,event_date""",
            f"""INSERT INTO hourly_stats SELECT dataset_id,toHour(toTimeZone(event_time,'Asia/Shanghai')),
                {counts} FROM behavior_events WHERE dataset_id={{dataset:String}} GROUP BY dataset_id,2""",
            f"""INSERT INTO user_stats SELECT dataset_id,user_id,count(),uniqExact(item_id),{counts},
                max(event_time_ms),maxIf(event_time_ms,behavior_type='buy'),min(event_date),groupUniqArray(event_date)
                FROM behavior_events WHERE dataset_id={{dataset:String}} GROUP BY dataset_id,user_id""",
            """INSERT INTO purchase_rankings SELECT dataset_id,'category',category_id,category_id,count(),uniqExact(user_id)
                FROM behavior_events WHERE dataset_id={dataset:String} AND behavior_type='buy'
                GROUP BY dataset_id,category_id""",
            """INSERT INTO purchase_rankings SELECT dataset_id,'product',item_id,argMax(category_id,event_time_ms),count(),uniqExact(user_id)
                FROM behavior_events WHERE dataset_id={dataset:String} AND behavior_type='buy'
                GROUP BY dataset_id,item_id""",
        ]
        with self.client() as client:
            for statement in statements:
                heartbeat()
                client.command(statement, parameters=p)
        result = self.query(
            """SELECT count() total,uniqExact(user_id) users,uniqExact(item_id) items,
            uniqExact(category_id) categories,min(event_time_ms) start_ms,max(event_time_ms) end_ms,
            countIf(behavior_type='pv') pv,countIf(behavior_type='fav') fav,
            countIf(behavior_type='cart') cart,countIf(behavior_type='buy') buy
            FROM behavior_events WHERE dataset_id={dataset:String}""",
            p,
        )[0]
        heartbeat()
        # Compute this expensive path once per completed immutable data version.
        window = result["end_ms"] - result["start_ms"] + 1
        funnel = self.query(
            """SELECT countIf(stage>=1) pv,countIf(stage>=2) cart,countIf(stage>=3) buy
            FROM (SELECT windowFunnel({window:UInt64},'strict_increase')
            (event_time_ms,behavior_type='pv',behavior_type='cart',behavior_type='buy') AS stage
            FROM behavior_events WHERE dataset_id={dataset:String} GROUP BY user_id,item_id)""",
            {**p, "window": window},
        )[0]
        result["funnel"] = [
            {"name": n, "value": funnel[k]}
            for n, k in [("浏览商品", "pv"), ("浏览后加购", "cart"), ("加购后购买", "buy")]
        ]
        return result

    def overview(self, dataset):
        summary = dataset.summary
        days = self.query(
            "SELECT day,pv,fav,cart,buy,users FROM daily_stats WHERE dataset_id={dataset:String} ORDER BY day",
            {"dataset": dataset.id},
        )
        return {
            "dataset_id": dataset.id,
            "counts": {t: summary.get(t, 0) for t in ("pv", "fav", "cart", "buy")},
            "total": summary.get("total", 0),
            "users": summary.get("users", 0),
            "items": summary.get("items", 0),
            "categories": summary.get("categories", 0),
            "days": days,
        }

    def metric(self, dataset, metric):
        p = {"dataset": dataset.id, "reference": dataset.end_ms or 0}
        if metric == "daily":
            return self.overview(dataset)["days"]
        if metric == "hourly":
            rows = self.query(
                "SELECT hour,pv,fav,cart,buy FROM hourly_stats WHERE dataset_id={dataset:String} ORDER BY hour", p
            )
            by_hour = {r["hour"]: r for r in rows}
            return [by_hour.get(i, {"hour": i, "pv": 0, "fav": 0, "cart": 0, "buy": 0}) for i in range(24)]
        if metric == "funnel":
            return dataset.summary.get("funnel", [])
        if metric == "rf":
            return self.query(
                f"SELECT {RF} name,count() value FROM user_stats WHERE dataset_id={{dataset:String}} GROUP BY name ORDER BY name",
                p,
            )
        if metric == "retention":
            fields = ",".join(f"countIf(has(active_days,first_day+{i})) AS d{i}" for i in range(8))
            rows = self.query(
                f"SELECT first_day day,count() users,{fields} FROM user_stats WHERE dataset_id={{dataset:String}} GROUP BY day ORDER BY day",
                p,
            )
            end = ms_day(dataset.end_ms)
            return [
                {
                    "day": row["day"],
                    "users": row["users"],
                    "retention": [
                        row[f"d{i}"] / row["users"] if row["day"] + timedelta(days=i) <= end else None for i in range(8)
                    ],
                }
                for row in rows
            ]
        return self.query(
            """SELECT id,category,count,users FROM purchase_rankings
            WHERE dataset_id={dataset:String} AND kind={kind:String} ORDER BY count DESC,id LIMIT 100""",
            {**p, "kind": metric},
        )

    def users(self, dataset, page, page_size, keyword="", segment=""):
        p = {
            "dataset": dataset.id,
            "reference": dataset.end_ms,
            "keyword": keyword,
            "segment": segment,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = "dataset_id={dataset:String} AND startsWith(user_id,{keyword:String})"
        if segment:
            where += f" AND {RF}={{segment:String}}"
        total = self.query(f"SELECT count() total FROM user_stats WHERE {where}", p)[0]["total"]
        rows = self.query(
            f"""SELECT user_id user,total,items itemCount,pv,fav,cart,buy,last_ms last,
            if(buy=0,NULL,toInt64(intDiv({{reference:UInt64}}-last_buy_ms,86400000))) recency,buy frequency,
            {RF} segment FROM user_stats WHERE {where} ORDER BY user_id LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}""",
            p,
        )
        for row in rows:
            row["counts"] = {k: row.pop(k) for k in ("pv", "fav", "cart", "buy")}
        return {"items": rows, "total": total, "page": page, "page_size": page_size, "dataset_id": dataset.id}

    def profile(self, dataset, user_id):
        p = {"dataset": dataset.id, "user": user_id, "reference": dataset.end_ms}
        found = self.query(
            f"""SELECT user_id user,total,items itemCount,pv,fav,cart,buy,last_ms last,
            if(buy=0,NULL,toInt64(intDiv({{reference:UInt64}}-last_buy_ms,86400000))) recency,
            buy frequency,{RF} segment FROM user_stats WHERE dataset_id={{dataset:String}} AND user_id={{user:String}}""",
            p,
        )
        if not found:
            return None
        result = found[0]
        result["counts"] = {k: result.pop(k) for k in ("pv", "fav", "cart", "buy")}
        result["cats"] = [
            [r["category"], r["events"]]
            for r in self.query(
                """SELECT category_id category,count() events
            FROM behavior_events WHERE dataset_id={dataset:String} AND user_id={user:String}
            GROUP BY category ORDER BY events DESC,category LIMIT 20""",
                p,
            )
        ]
        result["rows"] = self.query(
            """SELECT sequence id,user_id user,item_id item,category_id category,
            behavior_type type,event_time_ms t FROM behavior_events WHERE dataset_id={dataset:String}
            AND user_id={user:String} ORDER BY event_time_ms DESC,sequence DESC LIMIT 20""",
            p,
        )
        result["dataset_id"] = dataset.id
        return result

    def behaviors(self, dataset, page, page_size, keyword="", behavior="", from_day="", to_day=""):
        p = {"dataset": dataset.id, "limit": page_size, "offset": (page - 1) * page_size}
        where = "dataset_id={dataset:String}"
        for value, key, clause in [
            (keyword, "keyword", "(user_id={keyword:String} OR item_id={keyword:String})"),
            (behavior, "behavior", "behavior_type={behavior:String}"),
            (from_day, "from", "event_date>={from:Date}"),
            (to_day, "to", "event_date<={to:Date}"),
        ]:
            if value:
                where += " AND " + clause
                p[key] = value
        if not keyword:
            aggregate_where = "dataset_id={dataset:String}"
            if from_day:
                aggregate_where += " AND day>={from:Date}"
            if to_day:
                aggregate_where += " AND day<={to:Date}"
            field = {"": "pv+fav+cart+buy", "pv": "pv", "fav": "fav", "cart": "cart", "buy": "buy"}[behavior]
            total = self.query(f"SELECT sum({field}) total FROM daily_stats WHERE {aggregate_where}", p)[0]["total"]
        else:
            total = self.query(f"SELECT count() total FROM behavior_events WHERE {where}", p)[0]["total"]
        rows = self.query(
            f"""SELECT sequence id,user_id user,item_id item,category_id category,
            behavior_type type,event_time_ms t FROM behavior_events WHERE {where}
            ORDER BY event_date DESC,user_id DESC,event_time_ms DESC,sequence DESC
            LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}""",
            p,
        )
        return {"items": rows, "total": total, "page": page, "page_size": page_size, "dataset_id": dataset.id}
