import logging
import uuid

from clickhouse_connect.driver.exceptions import ClickHouseError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from . import api, auth
from .analytics import Analytics
from .config import Settings
from .db import Database
from .responses import ErrorReply
from .security import LoginLimiter

logger = logging.getLogger(__name__)


def error_response(request, status, message, details=None):
    return JSONResponse(
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Request-ID": getattr(request.state, "request_id", ""),
        },
        content={
            "error": {"code": status, "message": message, "details": details},
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


def create_app(settings=None, analytics=None):
    settings = settings or Settings()
    app = FastAPI(
        title="电商用户行为分析 API",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url="/api/redoc",
        description="Cookie 会话登录。登录返回 csrf_token；写操作需携带 X-CSRF-Token。账号与行为中的匿名用户相互独立。",
        responses={code: {"model": ErrorReply} for code in (400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503)},
    )
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.analytics = analytics or Analytics(settings)
    app.state.login_limiter = LoginLimiter()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return error_response(request, 403, "请求来源不在允许列表中")
            limit = (
                (settings.upload_limit_mb + 1) * 1024 * 1024
                if request.url.path == "/api/v1/datasets/import"
                else 1024 * 1024
            )
            try:
                if int(request.headers.get("content-length", "0")) > limit:
                    return error_response(request, 413, "请求体超过当前接口限制")
            except ValueError:
                return error_response(request, 400, "Content-Length 无效")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo raw inputs: they can contain passwords or private configuration.
        details = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return error_response(request, 422, "请求参数不符合接口约定", details)

    @app.exception_handler(SQLAlchemyError)
    async def sql_error(request, exc):
        logger.error("SQL operation failed (%s), request=%s", type(exc).__name__, request.state.request_id)
        return error_response(request, 503, "业务数据库操作失败，请检查本地配置与服务日志")

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        logger.exception("Unhandled API error; request=%s", request.state.request_id)
        return error_response(request, 500, "服务暂时无法完成请求，请凭请求编号查看日志")

    @app.exception_handler(ClickHouseError)
    async def analytics_error(request, exc):
        logger.error("Analytics operation failed (%s), request=%s", type(exc).__name__, request.state.request_id)
        return error_response(request, 503, "行为分析数据库不可用或查询超过资源限制，请缩小查询范围并查看日志")

    @app.get("/api/health", tags=["运行状态"])
    def health():
        status = {"mysql": False, "clickhouse": False}
        try:
            with app.state.db.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            status["mysql"] = True
        except Exception:
            pass
        try:
            status["clickhouse"] = app.state.analytics.ping()
        except Exception:
            pass
        return JSONResponse(
            {"status": "ready" if all(status.values()) else "degraded", "services": status},
            status_code=200 if all(status.values()) else 503,
        )

    app.include_router(auth.router)
    app.include_router(api.router)
    return app


app = create_app()
