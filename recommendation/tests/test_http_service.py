import math
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from test_bpr import CUTOFF, META, START, frame
from test_delivery import bundle as bundle
from test_delivery import save_bundle

from recommendation.assets import AssetError
from recommendation.bpr import BPRModel
from recommendation.engine import RecommendationEngine
from recommendation.http_service import MAX_RESPONSE_BYTES, create_app
from recommendation.itemcf import ItemCFConfig, ItemCFModel
from recommendation.popular import PopularModel
from recommendation.sample import sha256_file

VERSIONS = ("popular_v1", "itemcf_v1", "bpr_mf_buy_v1")
REQUEST = {"user_id": "buy", "dataset_id": META["dataset_id"], "model_version": "itemcf_v1", "limit": 10}


@pytest.fixture
def registry(tmp_path, bundle):
    return save_bundle(tmp_path, bundle)


@pytest.fixture
def client(registry):
    with TestClient(create_app(registry)) as client:
        yield client


def assert_error(response, status, code):
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message"}
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


@pytest.mark.parametrize("version", VERSIONS)
def test_contract_prediction_echo_filtered_order_finite_and_cold(client, bundle, version):
    engine = RecommendationEngine.from_models([model for model, _, _ in bundle])
    for user, limit in (("buy", 10), ("new-synthetic", 10), ("cart", 2)):
        payload = {**REQUEST, "user_id": user, "model_version": version, "limit": limit}
        response = client.post("/predict", json=payload)
        assert response.status_code == 200
        assert len(response.content) <= MAX_RESPONSE_BYTES
        result = response.json()
        assert result == engine.predict(**payload)
        assert set(result) == {"user_id", "dataset_id", "model_version", "items"}
        assert {key: result[key] for key in ("user_id", "dataset_id", "model_version")} == {
            key: payload[key] for key in ("user_id", "dataset_id", "model_version")
        }
        items = result["items"]
        assert 1 <= len(items) <= limit
        assert [item["rank"] for item in items] == list(range(1, len(items) + 1))
        assert len({item["item_id"] for item in items}) == len(items)
        for item in items:
            assert set(item) == {"item_id", "category_id", "rank", "score", "reason"}
            assert math.isfinite(item["score"]) and item["reason"]
        if user == "buy":
            assert all(item["item_id"] != "A" for item in items)
        if user == "new-synthetic":
            assert all("无用户历史" in item["reason"] for item in items)
    ready = client.get("/ready").json()
    assert ready["status"] == "ready" and set(ready["model_versions"]) == set(VERSIONS)
    assert ready["model_load_seconds"] >= 0
    assert client.get("/health").json() == ready


@pytest.mark.parametrize(
    "field,value,status,code",
    [
        ("user_id", "", 422, "invalid_user_id"),
        ("user_id", " anon ", 422, "invalid_user_id"),
        ("user_id", "a\nb", 422, "invalid_user_id"),
        ("user_id", 123, 422, "invalid_user_id"),
        ("user_id", "a" * 129, 422, "invalid_user_id"),
        ("limit", 0, 422, "invalid_limit"),
        ("limit", 11, 422, "invalid_limit"),
        ("limit", True, 422, "invalid_limit"),
        ("limit", "10", 422, "invalid_limit"),
        ("model_version", "bpr_v1", 404, "unknown_model"),
        ("dataset_id", "unbound-dataset", 409, "dataset_mismatch"),
        ("model_version", [], 422, "invalid_request"),
        ("asset_path", "other.asset", 422, "invalid_request"),
    ],
)
def test_request_errors(client, field, value, status, code):
    assert_error(client.post("/predict", json={**REQUEST, field: value}), status, code)


@pytest.mark.parametrize("field", REQUEST)
def test_all_contract_fields_are_required(client, field):
    data = {key: value for key, value in REQUEST.items() if key != field}
    assert_error(client.post("/predict", json=data), 422, "missing_field")


@pytest.mark.parametrize("body", [b'{"user_id":', b"\xff"])
def test_malformed_json_is_400(client, body):
    assert_error(
        client.post("/predict", content=body, headers={"Content-Type": "application/json"}),
        400,
        "invalid_json",
    )


def test_service_without_lifespan_is_unavailable(registry):
    client = TestClient(create_app(registry))
    for path in ("/health", "/ready"):
        assert_error(client.get(path), 503, "model_not_loaded")
    assert_error(client.post("/predict", json=REQUEST), 503, "model_not_loaded")
    client.close()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_corrupt_model_breaks_preload(registry, missing):
    asset = registry.parent / "itemcf_v1.asset"
    if missing:
        asset.unlink()
    else:
        asset.write_bytes(b"corrupt")
    code = "model_data_missing" if missing else "model_corrupt"
    with pytest.raises(RuntimeError, match=code), TestClient(create_app(registry)):
        pytest.fail("Broken registry started successfully")


@pytest.mark.parametrize("code", ["model_not_loaded", "model_corrupt", "model_identity_mismatch", "inference_error"])
def test_inference_failures_are_explicit_and_hide_internal_details(client, monkeypatch, code):
    def failure(**_payload):
        if code == "inference_error":
            raise RuntimeError("private local diagnostic")
        raise AssetError(code, "private local diagnostic")

    monkeypatch.setattr(client.app.state.engine, "predict_with_trace", failure)
    assert_error(client.post("/predict", json=REQUEST), 500 if code == "inference_error" else 503, code)
    assert "private local diagnostic" not in client.post("/predict", json=REQUEST).text


@pytest.mark.parametrize("empty_catalog", [True, False])
def test_no_candidate_returns_empty_success_with_diagnostic_header(tmp_path, empty_catalog):
    events = frame() if empty_catalog else frame(("buy", "A", "buy"))
    model = PopularModel.fit(events, CUTOFF, fit_start=START, **META)
    registry = save_bundle(tmp_path, [(model, {}, {})])
    with TestClient(create_app(registry)) as client:
        response = client.post("/predict", json={**REQUEST, "model_version": "popular_v1"})
    assert response.status_code == 200 and response.json()["items"] == []
    expected = "empty_candidate_catalog" if empty_catalog else "no_eligible_items"
    assert response.headers["X-Recommendation-Status"] == expected


def test_mixed_itemcf_trace_explains_actual_fill_without_changing_rank_or_scores(tmp_path):
    events = frame(
        ("buy", "A", "buy"),
        ("buy", "B", "pv"),
        ("other", "A", "pv"),
        ("other", "C", "pv"),
        ("third", "D", "buy"),
        ("third", "D", "buy"),
    )
    popular = PopularModel.fit(events, CUTOFF, fit_start=START, **META)
    parameters = {"neighbors": 3, "block_rows": 2}
    itemcf, summary = ItemCFModel.fit(events, popular, ItemCFConfig(**parameters), model_version="itemcf_v1")
    engine = RecommendationEngine.from_models([itemcf])
    expected, trace = engine.predict_with_trace(**REQUEST)
    assert trace == {
        "source": "itemcf_with_popular_fill",
        "item_sources": ["itemcf_cosine", "itemcf_cosine", "popular_fill"],
        "fallback_reason": "popular_fill",
    }
    assert [item["item_id"] for item in expected["items"]] == ["B", "C", "D"]
    assert expected["items"][-1]["score"] > expected["items"][0]["score"]
    registry = save_bundle(tmp_path, [(itemcf, parameters, summary)])
    with TestClient(create_app(registry)) as client:
        response = client.post("/predict", json=REQUEST)
    assert response.status_code == 200 and response.json() == expected


def test_concurrent_alternating_requests_do_not_fit_load_or_write_assets(registry, bundle, monkeypatch):
    def file_state():
        return {path.name: (sha256_file(path), path.stat().st_mtime_ns) for path in registry.parent.iterdir()}

    before = file_state()
    baseline = RecommendationEngine.from_models([model for model, _, _ in bundle])
    requests = [
        {**REQUEST, "user_id": user, "model_version": version}
        for user in ("buy", "solo", "new", "cart")
        for version in VERSIONS
    ]
    expected = [baseline.predict(**payload) for payload in requests]

    def forbid(*_args, **_kwargs):
        pytest.fail("inference attempted to train or reload")

    for cls in (PopularModel, ItemCFModel, BPRModel):
        monkeypatch.setattr(cls, "fit", forbid)
    monkeypatch.setattr(pq, "read_table", forbid)
    with TestClient(create_app(registry)) as client:
        monkeypatch.setattr(RecommendationEngine, "from_registry", forbid)
        monkeypatch.setattr(RecommendationEngine, "load", forbid)
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda payload: client.post("/predict", json=payload), requests * 4))
    assert all(response.status_code == 200 for response in responses)
    assert [response.json() for response in responses] == expected * 4
    assert file_state() == before


def test_openapi_describes_actual_error_envelope(client):
    schema = client.get("/openapi.json").json()
    for status in ("400", "404", "409", "422", "500", "503"):
        content = schema["paths"]["/predict"]["post"]["responses"][status]["content"]
        assert content["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
