"""Loopback-only HTTP adapter for preloaded recommendation assets."""

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from starlette.exceptions import HTTPException as StarletteHTTPException

from .assets import AssetError
from .engine import RecommendationEngine

DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "artifacts/p0-rec-04a/release-assets/registry.json"
MAX_RESPONSE_BYTES = 1024 * 1024
LOG = logging.getLogger("uvicorn.error")

ENGINE_ERRORS = {
    "invalid_user_id": (422, "Invalid user_id"),
    "invalid_limit": (422, "limit must be an integer between 1 and 10"),
    "unknown_model": (404, "Model version is not registered"),
    "dataset_mismatch": (409, "Dataset does not match the selected model"),
    "model_not_loaded": (503, "Model has not been loaded"),
    "model_data_missing": (503, "Model asset is missing"),
    "model_corrupt": (503, "Model asset is corrupt"),
    "model_identity_mismatch": (503, "Model asset identity is invalid"),
    "model_invalid_output": (500, "Model returned an invalid result"),
}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PredictRequest(Contract):
    user_id: Annotated[str, Field(min_length=1, max_length=128)]
    dataset_id: Annotated[str, Field(min_length=1, max_length=128)]
    model_version: Annotated[str, Field(min_length=1, max_length=64)]
    limit: Annotated[int, Field(ge=1, le=10)]


class PredictionItem(Contract):
    item_id: Annotated[str, Field(min_length=1, max_length=128)]
    category_id: Annotated[str, Field(max_length=128)]
    rank: Annotated[int, Field(ge=1, le=10)]
    score: FiniteFloat | None
    reason: Annotated[str, Field(max_length=200)]


class PredictionResponse(Contract):
    user_id: str
    dataset_id: str
    model_version: str
    items: Annotated[list[PredictionItem], Field(max_length=10)]


class ErrorDetail(Contract):
    code: str
    message: str


class ErrorResponse(Contract):
    error: ErrorDetail


def error_response(status, code, message):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(registry: Path = DEFAULT_REGISTRY) -> FastAPI:
    fixed_registry = Path(registry).resolve()

    @asynccontextmanager
    async def lifespan(app):
        started = perf_counter()
        try:
            engine = RecommendationEngine.from_registry(fixed_registry, preload=True)
            versions = engine.versions()
            if not versions or not all(engine.describe(version)["loaded"] for version in versions):
                raise AssetError("model_not_loaded", "Not all registered models were loaded")
        except AssetError as error:
            raise RuntimeError(f"Recommendation preload failed [{error.code}]: {error}") from error
        app.state.engine = engine
        app.state.ready = {
            "status": "ready",
            "model_versions": versions,
            "model_load_seconds": perf_counter() - started,
        }
        try:
            yield
        finally:
            app.state.ready = None
            app.state.engine = None

    app = FastAPI(title="Recommendation inference", lifespan=lifespan)
    app.state.engine = None
    app.state.ready = None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        errors = error.errors()
        if any(entry["type"] == "json_invalid" for entry in errors):
            return error_response(400, "invalid_json", "Request body must be valid JSON")
        if any(entry["type"] == "missing" for entry in errors):
            return error_response(422, "missing_field", "user_id, dataset_id, model_version and limit are required")
        for field in ("user_id", "limit"):
            if any(entry["loc"] == ("body", field) and entry["type"] != "extra_forbidden" for entry in errors):
                code = "invalid_" + field
                status, message = ENGINE_ERRORS[code]
                return error_response(status, code, message)
        return error_response(422, "invalid_request", "Request fields or types are invalid")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request, error):
        if error.status_code == 400:
            return error_response(400, "invalid_json", "Request body must be valid JSON")
        return JSONResponse(
            {"error": {"code": "http_error", "message": str(error.detail)}},
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.get("/health", response_model=None, responses={503: {"model": ErrorResponse}})
    @app.get("/ready", response_model=None, responses={503: {"model": ErrorResponse}})
    def readiness(request: Request):
        if request.app.state.ready is None or request.app.state.engine is None:
            return error_response(503, "model_not_loaded", "Recommendation service is not ready")
        return request.app.state.ready

    @app.post(
        "/predict",
        response_model=PredictionResponse,
        responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 422, 500, 503)},
    )
    def predict(payload: PredictRequest, request: Request):
        engine = request.app.state.engine
        if engine is None:
            return error_response(503, "model_not_loaded", "Recommendation service is not ready")
        try:
            result, trace = engine.predict_with_trace(**payload.model_dump())
            body = PredictionResponse.model_validate(result).model_dump()
            response = JSONResponse(body)
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise AssetError("model_invalid_output", "Serialized recommendation exceeds 1 MiB")
            if not body["items"]:
                reason = trace["fallback_reason"]
                response.headers["X-Recommendation-Status"] = (
                    "empty_candidate_catalog" if reason == "empty_candidate_catalog" else "no_eligible_items"
                )
            return response
        except AssetError as error:
            status, message = ENGINE_ERRORS.get(error.code, (500, "Recommendation inference failed"))
            if status >= 500:
                LOG.error("Recommendation inference failed [%s]", error.code)
            return error_response(status, error.code if error.code in ENGINE_ERRORS else "inference_error", message)
        except Exception:
            LOG.exception("Unexpected recommendation inference failure")
            return error_response(500, "inference_error", "Recommendation inference failed")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001, help="TCP port (0 selects a free local port)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    uvicorn.run(create_app(args.registry), host=args.host, port=args.port, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
