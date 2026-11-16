from contextlib import contextmanager

import httpx

from app.models import AppSetting, Dataset


def test_provider_contract_versions_and_fixed_configuration(admin, app, monkeypatch):
    with app.state.db.session() as db:
        dataset = Dataset(name="test", filename="test.csv", status="ready", created_by="admin")
        db.add(dataset)
        db.flush()
        db.get(AppSetting, "active_dataset").value = dataset.id
        db.commit()
        dataset_id = dataset.id
    admin.post("/api/v1/models", json={"id": "bpr_v1", "name": "组员模型", "dataset_id": dataset_id})
    admin.post("/api/v1/models/bpr_v1/activate")
    app.state.settings.recommender_url = "http://provider.test/predict"
    result = {
        "user_id": "u1",
        "dataset_id": dataset_id,
        "model_version": "bpr_v1",
        "items": [{"item_id": "p1", "category_id": "c1", "rank": 1, "score": 2.1, "reason": "测试夹具"}],
    }

    @contextmanager
    def fake_stream(method, url, **kwargs):
        assert method == "POST"
        assert kwargs["json"] == {"user_id": "u1", "dataset_id": dataset_id, "model_version": "bpr_v1", "limit": 10}
        yield httpx.Response(200, json=result, request=httpx.Request(method, url))

    monkeypatch.setattr("app.api.httpx.stream", fake_stream)
    response = admin.get("/api/v1/recommendations?user_id=u1")
    assert response.status_code == 200
    assert response.json()["items"][0]["score"] == 2.1
    result["dataset_id"] = "different-version"
    assert admin.get("/api/v1/recommendations?user_id=u1").status_code == 502
    result["dataset_id"] = dataset_id
    result["items"][0]["rank"] = 2
    assert admin.get("/api/v1/recommendations?user_id=u1").status_code == 502
