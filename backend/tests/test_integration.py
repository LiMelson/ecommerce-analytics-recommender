"""Real database integration. Opt in with RUN_DB_TESTS=1; never reuse project tables."""

import os
import uuid

import pytest
from conftest import TEST_PASSWORD, login
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.analytics import Analytics
from app.cli import initialize
from app.config import Settings
from app.main import create_app
from app.models import Account, Job
from app.security import PASSWORDS
from app.worker import claim, run_job

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Set RUN_DB_TESTS=1 for isolated real DB integration"),
]


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    original = Settings()
    name = "commerce_test_" + uuid.uuid4().hex[:12]
    previous = {key: os.environ.get(key) for key in ("MYSQL_DATABASE", "CLICKHOUSE_DATABASE")}
    os.environ["MYSQL_DATABASE"] = name
    os.environ["CLICKHOUSE_DATABASE"] = name
    settings = Settings(instance_dir=tmp_path_factory.mktemp("realdb"), database_url=None)
    server = create_engine(original.sql_url(False))
    try:
        initialize(settings)
        app = create_app(settings)
        with app.state.db.session() as db:
            db.get(Account, "admin").password_hash = PASSWORDS.hash(TEST_PASSWORD)
            db.commit()
        with TestClient(app) as client:
            login(client)
            yield app, client
        app.state.db.engine.dispose()
    finally:
        assert name.startswith("commerce_test_") and len(name) == 26
        with server.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        with Analytics(original).client("default") as ch:
            ch.command(f"DROP DATABASE IF EXISTS `{name}`")
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.dispose()


def execute_upload(app, client, name, content):
    result = client.post("/api/v1/datasets/import", files={"file": (name, content)})
    assert result.status_code == 202, result.text
    job_id = claim(app.state.db.session)
    assert job_id == result.json()["job"]["id"]
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "ready", status
    return result.json()["dataset"]["id"]


def test_csv_to_real_mysql_clickhouse_analytics_and_export(real):
    app, client = real
    rows = "user_id,item_id,category_id,behavior_type,timestamp\n" + "\n".join(
        [
            "u1,p1,c1,pv,2026-11-01T23:59:00+08:00",
            "u1,p1,c1,cart,2026-11-02T00:01:00+08:00",
            "u1,p1,c1,buy,2026-11-02T00:02:00+08:00",
            "u2,p2,c2,buy,2026-11-01T12:00:00+08:00",
            "u2,p2,c2,pv,2026-11-01T13:00:00+08:00",
            "u2,p2,c2,cart,2026-11-01T14:00:00+08:00",
            "u2,p3,c2,pv,2026-11-01T15:00:00+08:00",
            "u2,p3,c2,cart,2026-11-01T15:00:00+08:00",
            "u2,p3,c2,buy,2026-11-01T15:00:00+08:00",
            "u3,p4,c3,pv,2026-11-02T16:00:00+08:00",
            "u3,p4,c3,pv,2026-11-02T16:00:00+08:00",
        ]
    )
    dataset = execute_upload(app, client, "events.csv", rows.encode())
    assert client.get("/api/health").status_code == 200
    overview = client.get("/api/v1/overview").json()
    assert overview["dataset_id"] == dataset
    assert overview["total"] == 11 and overview["users"] == 3
    assert overview["counts"] == {"pv": 5, "fav": 0, "cart": 3, "buy": 3}
    funnel = client.get("/api/v1/analytics/funnel").json()["items"]
    assert [r["value"] for r in funnel] == [4, 2, 1]
    hours = client.get("/api/v1/analytics/hourly").json()["items"]
    assert len(hours) == 24 and hours[0]["buy"] == 1
    retention = client.get("/api/v1/analytics/retention").json()["items"]
    assert retention[0]["retention"][1] == 0.5
    assert retention[-1]["retention"][1] is None
    assert sum(row["value"] for row in client.get("/api/v1/analytics/rf").json()["items"]) == 3
    profile = client.get("/api/v1/users/u3/profile").json()
    assert profile["total"] == 2 and profile["recency"] is None
    assert client.get("/api/v1/users?keyword=u&page_size=1").json()["total"] == 3
    assert client.get("/api/v1/behaviors?keyword=u1&behavior=buy").json()["total"] == 1
    assert client.get("/api/v1/behaviors?behavior=buy&from_day=2026-11-02").json()["total"] == 1
    assert client.get("/api/v1/behaviors?to_day=2026-11-01").json()["total"] == 7
    assert client.get("/api/v1/behaviors?keyword=%27%20OR%201%3D1").json()["total"] == 0
    assert client.get("/api/v1/analytics/category").json()["items"][0]["count"] == 2
    assert client.get("/api/v1/recommendations?user_id=u1").status_code == 503
    job = client.post("/api/v1/behaviors/export?keyword=u1").json()
    assert claim(app.state.db.session) == job["id"]
    run_job(job["id"], app.state.db.session, app.state.analytics, app.state.settings)
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "ready"
    export = client.get(f"/api/v1/jobs/{job['id']}/download")
    assert export.status_code == 200 and export.content.decode("utf-8-sig").count("u1") == 3


def test_parquet_and_version_activation_and_failed_partial_import(real):
    import pyarrow as pa
    import pyarrow.parquet as pq

    app, client = real
    before = client.get("/api/v1/datasets/active").json()["dataset"]["id"]
    path = app.state.settings.instance_dir / "inbox" / "events.parquet"
    pq.write_table(
        pa.table({"user_id": ["puser"], "item_id": ["pitem"], "behavior_type": ["pv"], "timestamp": [1793462400000]}),
        path,
    )
    queued = client.post("/api/v1/datasets/import-local", json={"relative_path": "events.parquet", "activate": False})
    assert queued.status_code == 202
    job_id = claim(app.state.db.session)
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "ready"
    assert client.get("/api/v1/datasets/active").json()["dataset"]["id"] == before
    assert client.post(f"/api/v1/datasets/{queued.json()['dataset']['id']}/activate").status_code == 200
    assert client.get("/api/v1/overview").json()["total"] == 1
    app.state.settings.import_batch_size = 100
    bad = "user_id,item_id,behavior_type,timestamp\n" + "u,p,pv,1793462400000\n" * 100 + "u,p,invalid,1793462400000\n"
    client.post("/api/v1/datasets/import", files={"file": ("partial.csv", bad.encode())})
    job_id = claim(app.state.db.session)
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "failed"
    assert client.get("/api/v1/overview").json()["total"] == 1
    with app.state.db.session() as db:
        assert db.get(Job, job_id).processed_rows == 100


def test_real_queue_claim_has_no_double_execution(real):
    from concurrent.futures import ThreadPoolExecutor

    app, client = real
    body = b"user_id,item_id,behavior_type,timestamp\nu,p,pv,1793462400000\n"
    result = client.post("/api/v1/datasets/import", files={"file": ("concurrent.csv", body)}).json()
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: claim(app.state.db.session), range(2)))
    assert claims.count(result["job"]["id"]) == 1 and claims.count(None) == 1
    run_job(result["job"]["id"], app.state.db.session, app.state.analytics, app.state.settings)


@pytest.mark.scale
@pytest.mark.skipif(os.getenv("RUN_SCALE_TESTS") != "1", reason="Set RUN_SCALE_TESTS=1 for 1M generated rows")
def test_one_million_rows_bounded_import_and_serving(real):
    import json
    import time
    import tracemalloc

    from app.config import ROOT

    app, client = real
    app.state.settings.import_batch_size = 10000
    path = app.state.settings.instance_dir / "inbox" / "million.csv"
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write("user_id,item_id,category_id,behavior_type,timestamp\n")
        for i in range(1000000):
            group = i // 3
            target.write(
                f"u{group % 10000:05},p{group % 5000:05},c{group % 30:02},{('pv', 'cart', 'buy')[i % 3]},{1793462400000 + i * 1000}\n"
            )
    tracemalloc.start()
    start = time.perf_counter()
    result = client.post("/api/v1/datasets/import-local", json={"relative_path": path.name}).json()
    job_id = claim(app.state.db.session)
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "ready", status
    report = {
        "rows": 1000000,
        "file_bytes": path.stat().st_size,
        "import_and_summary_seconds": round(elapsed, 3),
        "python_peak_traced_mb": round(peak / 1024**2, 2),
        "dataset_id": result["dataset"]["id"],
        "queries": {},
    }
    for route in [
        "/overview",
        "/analytics/retention",
        "/analytics/rf",
        "/behaviors?page_size=10",
        "/users/u00001/profile",
    ]:
        started = time.perf_counter()
        response = client.get("/api/v1" + route)
        assert response.status_code == 200, response.text
        report["queries"][route] = {
            "seconds": round(time.perf_counter() - started, 3),
            "response_bytes": len(response.content),
        }
    assert client.get("/api/v1/overview").json()["total"] == 1000000
    (ROOT / "instance").mkdir(exist_ok=True)
    (ROOT / "instance/scale-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
