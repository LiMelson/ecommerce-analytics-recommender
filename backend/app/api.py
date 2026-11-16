import hashlib
import logging
import uuid
from datetime import date
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from . import responses as o
from . import schemas as s
from .common import active_dataset, dataset_json, job_json, pagination
from .db import get_db
from .importing import BEHAVIORS, ImportValidationError, queue_import, safe_inbox
from .models import AppSetting, AuditLog, Dataset, Job, Message, ModelVersion, audit, now
from .security import principal, require

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
Page = Query(1, ge=1, le=10000)
Size = Query(20, ge=1, le=100)


def filters(keyword, behavior, from_day, to_day):
    if behavior and behavior not in BEHAVIORS:
        raise HTTPException(422, "行为类型无效")
    if from_day and to_day and from_day > to_day:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    return {
        "keyword": keyword,
        "behavior": behavior,
        "from": from_day.isoformat() if from_day else "",
        "to": to_day.isoformat() if to_day else "",
    }


def can_manage_jobs(user):
    if not set(user["permissions"]) & {"数据导入", "任务管理"}:
        raise HTTPException(403, "当前账号没有任务访问权限")


def own_job(db, job_id, user):
    can_manage_jobs(user)
    job = db.get(Job, job_id)
    if not job or (job.created_by != user["account"].username and "任务管理" not in user["permissions"]):
        raise HTTPException(404, "任务不存在")
    return job


@router.get("/datasets", tags=["数据集"], response_model=o.Page[o.Dataset])
def datasets(page: int = Page, page_size: int = Size, user=Depends(require("数据概览")), db=Depends(get_db)):
    active = db.get(AppSetting, "active_dataset")
    return pagination(
        db,
        select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc()),
        page,
        page_size,
        lambda d: dataset_json(d, active.value if active else ""),
    )


@router.get("/datasets/active", tags=["数据集"], response_model=o.ActiveDataset)
def current_dataset(user=Depends(require("数据概览")), db=Depends(get_db)):
    setting = db.get(AppSetting, "active_dataset")
    dataset = db.get(Dataset, setting.value) if setting and setting.value else None
    return {"dataset": dataset_json(dataset, dataset.id) if dataset else None}


@router.get("/datasets/template", tags=["数据集"])
def template(user=Depends(require("数据导入"))):
    return Response(
        "user_id,item_id,category_id,behavior_type,timestamp\r\nu001,p001,c001,pv,2026-11-01T08:00:00+08:00\r\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="behavior-template.csv"'},
    )


@router.get("/datasets/inbox", tags=["数据集"])
def inbox_files(request: Request, user=Depends(require("任务管理"))):
    root = (request.app.state.settings.instance_dir / "inbox").resolve()
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in {".csv", ".parquet"} and path.resolve().is_relative_to(root):
            stat = path.stat()
            items.append({"relative_path": path.name, "bytes": stat.st_size, "modified_at": int(stat.st_mtime)})
    items.sort(key=lambda p: p["modified_at"], reverse=True)
    return {"items": items[:200], "limit": 200}


@router.post("/datasets/import", status_code=202, tags=["数据集"], response_model=o.ImportResult)
def upload(
    request: Request,
    file: UploadFile = File(...),
    name: str = "",
    activate: bool = True,
    user=Depends(require("数据导入")),
    db=Depends(get_db),
):
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".csv", ".parquet"}:
        raise HTTPException(422, "只接受 CSV 或 Parquet 文件")
    settings = request.app.state.settings
    root = settings.instance_dir / "inbox"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    size, digest = 0, hashlib.sha256()
    try:
        with path.open("wb") as target:
            for chunk in iter(lambda: file.file.read(1024 * 1024), b""):
                size += len(chunk)
                if size > settings.upload_limit_mb * 1024 * 1024:
                    raise HTTPException(
                        413, f"网页上传上限 {settings.upload_limit_mb} MB，更大的文件请使用服务器文件导入"
                    )
                digest.update(chunk)
                target.write(chunk)
        if not size:
            raise HTTPException(422, "文件为空")
        dataset, job = queue_import(db, user["account"].username, path, name[:100] or filename[:100], activate)
        dataset.filename, dataset.sha256 = filename[:200], digest.hexdigest()
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    return {"dataset": dataset_json(dataset), "job": job_json(job)}


@router.post("/datasets/import-local", status_code=202, tags=["数据集"], response_model=o.ImportResult)
def import_local(body: s.FileImport, request: Request, user=Depends(require("任务管理")), db=Depends(get_db)):
    try:
        path = safe_inbox(request.app.state.settings, body.relative_path)
    except ImportValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    dataset, job = queue_import(db, user["account"].username, path, body.name, body.activate)
    return {"dataset": dataset_json(dataset), "job": job_json(job)}


@router.post("/datasets/{dataset_id}/activate", tags=["数据集"], response_model=o.Dataset)
def activate_dataset(dataset_id: str, user=Depends(require("数据导入")), db=Depends(get_db)):
    dataset = active_dataset(db, dataset_id)
    setting = db.scalar(select(AppSetting).where(AppSetting.key == "active_dataset").with_for_update())
    setting.value = dataset.id
    audit(db, user["account"].username, "数据管理", "切换数据集", dataset.id)
    db.commit()
    return dataset_json(dataset, dataset.id)


@router.get("/overview", tags=["统计与画像"], response_model=o.Overview)
def overview(request: Request, dataset_id: str | None = None, user=Depends(require("数据概览")), db=Depends(get_db)):
    return request.app.state.analytics.overview(active_dataset(db, dataset_id))


@router.get("/analytics/{metric}", tags=["统计与画像"], response_model=o.Metric)
def metric(
    metric: str, request: Request, dataset_id: str | None = None, user=Depends(require("行为分析")), db=Depends(get_db)
):
    if metric not in {"daily", "hourly", "funnel", "rf", "retention", "category", "product"}:
        raise HTTPException(404, "分析指标不存在")
    dataset = active_dataset(db, dataset_id)
    return {"dataset_id": dataset.id, "items": request.app.state.analytics.metric(dataset, metric)}


@router.get("/users", tags=["统计与画像"], response_model=o.DatasetPage[o.UserSummary])
def users(
    request: Request,
    page: int = Page,
    page_size: int = Size,
    keyword: str = Query("", max_length=128),
    segment: str = Query("", max_length=40),
    dataset_id: str | None = None,
    user=Depends(require("行为分析")),
    db=Depends(get_db),
):
    return request.app.state.analytics.users(active_dataset(db, dataset_id), page, page_size, keyword, segment)


@router.get("/users/{user_id}/profile", tags=["统计与画像"], response_model=o.UserProfile)
def profile(
    user_id: str, request: Request, dataset_id: str | None = None, user=Depends(require("行为分析")), db=Depends(get_db)
):
    if len(user_id) > 128:
        raise HTTPException(422, "用户 ID 过长")
    result = request.app.state.analytics.profile(active_dataset(db, dataset_id), user_id)
    if not result:
        raise HTTPException(404, "该用户在当前数据集没有行为记录")
    return result


@router.get("/behaviors", tags=["行为明细"], response_model=o.DatasetPage[o.Behavior])
def behaviors(
    request: Request,
    page: int = Page,
    page_size: int = Size,
    keyword: str = Query("", max_length=128),
    behavior: str = "",
    from_day: date | None = None,
    to_day: date | None = None,
    dataset_id: str | None = None,
    user=Depends(require("行为分析")),
    db=Depends(get_db),
):
    f = filters(keyword, behavior, from_day, to_day)
    return request.app.state.analytics.behaviors(
        active_dataset(db, dataset_id), page, page_size, f["keyword"], f["behavior"], f["from"], f["to"]
    )


@router.post("/behaviors/export", status_code=202, tags=["行为明细"], response_model=o.Job)
def export(
    keyword: str = Query("", max_length=128),
    behavior: str = "",
    from_day: date | None = None,
    to_day: date | None = None,
    dataset_id: str | None = None,
    user=Depends(require("数据导入")),
    db=Depends(get_db),
):
    payload = filters(keyword, behavior, from_day, to_day)
    dataset = active_dataset(db, dataset_id)
    job = Job(kind="export", dataset_id=dataset.id, created_by=user["account"].username, payload=payload)
    db.add(job)
    audit(db, user["account"].username, "数据管理", "提交导出任务", dataset.id)
    db.commit()
    return job_json(job)


@router.get("/jobs", tags=["后台任务"], response_model=o.Page[o.Job])
def jobs(page: int = Page, page_size: int = Size, user=Depends(principal), db=Depends(get_db)):
    can_manage_jobs(user)
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
    if "任务管理" not in user["permissions"]:
        query = query.where(Job.created_by == user["account"].username)
    return pagination(db, query, page, page_size, job_json)


@router.get("/jobs/{job_id}", tags=["后台任务"], response_model=o.Job)
def job_status(job_id: str, user=Depends(principal), db=Depends(get_db)):
    return job_json(own_job(db, job_id, user))


@router.post("/jobs/{job_id}/recover", tags=["后台任务"], response_model=o.Job)
def recover_job(job_id: str, user=Depends(require("任务管理")), db=Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status != "running" or now() - job.updated_at < 600:
        raise HTTPException(409, "只能结束超过十分钟无心跳的运行任务")
    job.status, job.error, job.updated_at = "failed", "任务进程中断，请重新提交为新数据版本", now()
    if job.kind == "import":
        db.get(Dataset, job.dataset_id).status = "failed"
    audit(db, user["account"].username, "数据管理", "结束中断任务", job.id)
    db.commit()
    return job_json(job)


@router.get("/jobs/{job_id}/download", tags=["后台任务"])
def download(job_id: str, request: Request, user=Depends(principal), db=Depends(get_db)):
    job = own_job(db, job_id, user)
    if job.kind != "export" or job.status != "ready":
        raise HTTPException(409, "导出文件尚未就绪")
    path = request.app.state.settings.instance_dir / "exports" / f"{job.id}.csv"
    if not path.is_file():
        raise HTTPException(404, "导出文件已清理，请重新导出")
    return FileResponse(path, filename="用户行为数据.csv", media_type="text/csv")


@router.get("/logs", tags=["日志与留言"], response_model=o.Page[o.Log])
def logs(
    page: int = Page,
    page_size: int = Size,
    module: str = Query("", max_length=40),
    user=Depends(principal),
    db=Depends(get_db),
):
    query = select(AuditLog).order_by(AuditLog.id.desc())
    if module:
        query = query.where(AuditLog.module == module)
    if "账号管理" not in user["permissions"]:
        query = query.where(AuditLog.actor == user["account"].username)
    return pagination(
        db,
        query,
        page,
        page_size,
        lambda log: {
            "id": log.id,
            "actor": log.actor,
            "module": log.module,
            "action": log.action,
            "detail": log.detail,
            "status": log.status,
            "created_at": log.created_at,
        },
    )


@router.get("/messages", tags=["日志与留言"], response_model=o.Page[o.Message])
def messages(page: int = Page, page_size: int = Size, user=Depends(principal), db=Depends(get_db)):
    return pagination(db, select(Message).order_by(Message.id.desc()), page, page_size, message_json)


def message_json(message):
    return {
        "id": message.id,
        "author_id": message.author_id,
        "name": message.author_name,
        "type": message.type,
        "content": message.content,
        "done": message.done,
        "created_at": message.created_at,
    }


@router.post("/messages", status_code=201, tags=["日志与留言"], response_model=o.Message)
def post_message(body: s.MessageCreate, user=Depends(principal), db=Depends(get_db)):
    message = Message(author_id=user["account"].username, author_name=user["account"].name, **body.model_dump())
    db.add(message)
    audit(db, user["account"].username, "系统", "发布留言")
    db.commit()
    return message_json(message)


@router.patch("/messages/{message_id}", tags=["日志与留言"], response_model=o.Message)
def mark_message(message_id: int, body: s.MessageUpdate, user=Depends(principal), db=Depends(get_db)):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(404, "留言不存在")
    if message.author_id != user["account"].username and "账号管理" not in user["permissions"]:
        raise HTTPException(403, "只能处理自己发布的留言")
    message.done = body.done
    audit(db, user["account"].username, "系统", "修改留言状态", message.id)
    db.commit()
    return message_json(message)


@router.get("/models/active", tags=["算法接入"], response_model=o.ActiveModel)
def active_model(user=Depends(require("推荐查看")), db=Depends(get_db)):
    setting = db.get(AppSetting, "active_model")
    model = db.get(ModelVersion, setting.value) if setting and setting.value else None
    if not model:
        return {"status": "not_connected", "model": None}
    current = db.get(AppSetting, "active_dataset")
    return {
        "status": "registered" if current and current.value == model.dataset_id else "incompatible",
        "model": model_json(model),
    }


def model_json(model):
    return {
        "id": model.id,
        "dataset_id": model.dataset_id,
        "name": model.name,
        "metrics": model.metrics,
        "note": model.note,
        "created_at": model.created_at,
    }


@router.post("/models", status_code=201, tags=["算法接入"], response_model=o.Model)
def register_model(body: s.ModelCreate, user=Depends(require("任务管理")), db=Depends(get_db)):
    from sqlalchemy.exc import IntegrityError

    active_dataset(db, body.dataset_id)
    model = ModelVersion(**body.model_dump(), created_by=user["account"].username)
    db.add(model)
    audit(db, user["account"].username, "智能推荐", "登记模型版本", body.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "模型版本已存在") from None
    return model_json(model)


@router.post("/models/{model_id}/activate", tags=["算法接入"], response_model=o.Model)
def activate_model(model_id: str, user=Depends(require("任务管理")), db=Depends(get_db)):
    model = db.get(ModelVersion, model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    dataset = active_dataset(db)
    if dataset.id != model.dataset_id:
        raise HTTPException(409, "模型训练数据版本与当前数据集不一致")
    db.get(AppSetting, "active_model").value = model.id
    audit(db, user["account"].username, "智能推荐", "启用模型版本", model.id)
    db.commit()
    return model_json(model)


@router.get("/recommendations", response_model=s.RecommendationResult, tags=["算法接入"])
def recommendations(
    request: Request,
    user_id: str = Query(min_length=1, max_length=128),
    user=Depends(require("推荐查看")),
    db=Depends(get_db),
):
    dataset = active_dataset(db)
    setting = db.get(AppSetting, "active_model")
    model = db.get(ModelVersion, setting.value) if setting and setting.value else None
    url = request.app.state.settings.recommender_url
    if model and model.dataset_id != dataset.id:
        raise HTTPException(409, "当前数据集与活动模型不兼容")
    if not model or not url:
        raise HTTPException(503, "推荐算法服务尚未接入，请等待算法组交付模型")
    payload = {"user_id": user_id, "dataset_id": dataset.id, "model_version": model.id, "limit": 10}
    try:
        with httpx.stream("POST", url, json=payload, timeout=5, follow_redirects=False) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 1024 * 1024:
                    raise ValueError("provider response too large")
                chunks.append(chunk)
            result = s.RecommendationResult.model_validate_json(b"".join(chunks))
        if (result.user_id, result.dataset_id, result.model_version) != (user_id, dataset.id, model.id):
            raise ValueError("provider version mismatch")
        if [i.rank for i in result.items] != list(range(1, len(result.items) + 1)) or len(
            {i.item_id for i in result.items}
        ) != len(result.items):
            raise ValueError("provider ordering or uniqueness mismatch")
    except Exception:
        logger.exception("Recommendation provider failed")
        raise HTTPException(502, "算法服务不可用或返回格式不符合接入约定") from None
    audit(db, user["account"].username, "智能推荐", "查询推荐", f"模型 {model.id}；用户 {user_id}")
    db.commit()
    return result
