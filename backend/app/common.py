from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from .models import AppSetting, Dataset


def pagination(db, query, page, page_size, serializer):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "page 至少为 1，page_size 范围为 1–100")
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serializer(item) for item in items], "total": total, "page": page, "page_size": page_size}


def active_dataset(db, dataset_id=None):
    if not dataset_id:
        setting = db.get(AppSetting, "active_dataset")
        dataset_id = setting.value if setting else None
    dataset = db.get(Dataset, dataset_id) if dataset_id else None
    if not dataset:
        raise HTTPException(404, "尚未选择可用数据集，请先导入数据")
    if dataset.status != "ready":
        raise HTTPException(409, "数据集尚未导入完成")
    return dataset


def dataset_json(dataset, active_id=""):
    return {
        "id": dataset.id,
        "name": dataset.name,
        "filename": dataset.filename,
        "source": dataset.source,
        "is_demo": dataset.is_demo,
        "status": dataset.status,
        "row_count": dataset.row_count,
        "start_ms": dataset.start_ms,
        "end_ms": dataset.end_ms,
        "sha256": dataset.sha256,
        "summary": dataset.summary,
        "active": dataset.id == active_id,
        "created_at": dataset.created_at,
    }


def job_json(job):
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "dataset_id": job.dataset_id,
        "processed_rows": job.processed_rows,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def ms_day(ms):
    from datetime import timedelta

    return datetime.fromtimestamp(ms / 1000, timezone(timedelta(hours=8))).date()
