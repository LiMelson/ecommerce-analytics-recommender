import pytest

from app.importing import ImportValidationError, parse_record, records, safe_inbox
from app.models import AppSetting, Dataset, Job
from app.worker import claim, run_job

HEADER = "user_id,item_id,category_id,behavior_type,timestamp\n"


def test_canonical_format_keeps_duplicates_and_timezone(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text(
        HEADER + "u1,p1,c1,pv,2026-11-01T00:00:00+08:00\nu1,p1,c1,pv,2026-11-01T00:00:00+08:00\n", encoding="utf-8"
    )
    result = list(records(path, 100))
    assert len(result) == 2
    assert result[0][1] == result[1][1]
    assert result[0][1][-1] == 1793462400000


@pytest.mark.parametrize(
    "field,value",
    [("behavior_type", "view"), ("timestamp", "2026-11-01 00:00:00"), ("user_id", ""), ("timestamp", "0")],
)
def test_no_silent_data_cleaning(field, value):
    record = {"user_id": "u", "item_id": "p", "behavior_type": "pv", "timestamp": "1793462400"}
    record[field] = value
    with pytest.raises(ImportValidationError):
        parse_record(record, 1)


def test_header_and_local_file_boundary(tmp_path, app):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (tmp_path / "outside.csv").write_text(HEADER)
    with pytest.raises(ImportValidationError):
        safe_inbox(app.state.settings, "../outside.csv")
    (inbox / "bad.csv").write_text("user_id,item_id,behavior_type,timestamp,timestamp\nu,p,pv,1,2")
    with pytest.raises(ImportValidationError):
        list(records(inbox / "bad.csv", 100))


def test_invalid_import_never_publishes_partial_data(admin, app):
    uploaded = admin.post(
        "/api/v1/datasets/import",
        files={"file": ("invalid.csv", (HEADER + "u,p,c,pv,1793462400\nu,p,c,view,1793462400\n").encode())},
    )
    assert uploaded.status_code == 202
    job_id = claim(app.state.db.session)
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    job = admin.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "第 2 条" in job["error"]
    assert admin.get("/api/v1/datasets/active").json()["dataset"] is None
    assert admin.post(f"/api/v1/datasets/{uploaded.json()['dataset']['id']}/activate").status_code == 409


def test_queued_job_claimed_once_and_only_owner_can_read(admin, app):
    result = admin.post(
        "/api/v1/datasets/import", files={"file": ("ok.csv", (HEADER + "u,p,c,pv,1793462400\n").encode())}
    ).json()
    assert claim(app.state.db.session) == result["job"]["id"]
    assert claim(app.state.db.session) is None
    assert admin.post(f"/api/v1/jobs/{result['job']['id']}/recover").status_code == 409


def test_incompatible_model_and_no_fake_recommendations(admin, app):
    with app.state.db.session() as db:
        dataset = Dataset(name="unit", filename="unit.csv", created_by="admin", status="ready", start_ms=1, end_ms=2)
        db.add(dataset)
        db.flush()
        db.get(AppSetting, "active_dataset").value = dataset.id
        db.commit()
        dataset_id = dataset.id
    assert admin.get("/api/v1/recommendations?user_id=u1").status_code == 503
    assert (
        admin.post("/api/v1/models", json={"id": "model1", "dataset_id": dataset_id, "name": "组员模型"}).status_code
        == 201
    )
    assert admin.post("/api/v1/models/model1/activate").status_code == 200
    assert admin.get("/api/v1/models/active").json()["status"] == "registered"
    assert admin.get("/api/v1/recommendations?user_id=u1").status_code == 503
    with app.state.db.session() as db:
        newer = Dataset(name="new", filename="new.csv", created_by="admin", status="ready")
        db.add(newer)
        db.flush()
        db.get(AppSetting, "active_dataset").value = newer.id
        db.commit()
    assert admin.get("/api/v1/models/active").json()["status"] == "incompatible"
    assert admin.get("/api/v1/recommendations?user_id=u1").status_code == 409


def test_server_file_listing_and_boundary(admin, app):
    root = app.state.settings.instance_dir / "inbox"
    root.mkdir()
    (root / "dataset.csv").write_text(HEADER, encoding="utf-8")
    (root / "unrelated.txt").write_text("not dataset")
    response = admin.get("/api/v1/datasets/inbox")
    assert response.status_code == 200
    assert [item["relative_path"] for item in response.json()["items"]] == ["dataset.csv"]
    assert admin.post("/api/v1/datasets/import-local", json={"relative_path": "../.env"}).status_code == 422
    assert (
        admin.post(
            "/api/v1/datasets/import-local", json={"relative_path": r"\\untrusted.invalid\share\data.csv"}
        ).status_code
        == 422
    )
    assert admin.post("/api/v1/datasets/import-local", json={"relative_path": "C:/outside/data.csv"}).status_code == 422


def test_recovered_job_cannot_publish_late_result(admin, app):
    admin.post("/api/v1/datasets/import", files={"file": ("ok.csv", (HEADER + "u,p,c,pv,1793462400\n").encode())})
    job_id = claim(app.state.db.session)
    with app.state.db.session() as db:
        db.get(Job, job_id).updated_at = 1
        db.commit()
    assert admin.post(f"/api/v1/jobs/{job_id}/recover").status_code == 200
    run_job(job_id, app.state.db.session, app.state.analytics, app.state.settings)
    assert admin.get("/api/v1/datasets/active").json()["dataset"] is None
