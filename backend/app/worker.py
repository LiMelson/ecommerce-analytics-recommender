"""Durable SQL job queue; run separately: python -m app.worker."""

import csv
import logging
import time
from pathlib import Path

from sqlalchemy import select

from .analytics import Analytics
from .config import Settings
from .db import Database
from .importing import ImportValidationError, file_hash, records
from .models import AppSetting, Dataset, Job, audit, now

logger = logging.getLogger(__name__)


def claim(db_factory):
    with db_factory() as db:
        job = db.scalar(
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status = "running"
        job.updated_at = now()
        if job.kind == "import":
            db.get(Dataset, job.dataset_id).status = "importing"
        db.commit()
        return job.id


def run_job(job_id, db_factory, analytics, settings):
    with db_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.status != "running":
            return
        dataset_id, payload, actor, kind = job.dataset_id, job.payload, job.created_by, job.kind

    def heartbeat(count=None):
        with db_factory() as db:
            job = db.get(Job, job_id)
            if job.status != "running":
                raise ImportValidationError("任务已被结束，请重新提交")
            job.updated_at = now()
            if count is not None:
                job.processed_rows = count
            db.commit()

    try:
        if kind == "export":
            export_file(job_id, dataset_id, payload, db_factory, analytics, settings, heartbeat)
            return
        path = Path(payload["path"]).resolve()
        inbox = (settings.instance_dir / "inbox").resolve()
        if not path.is_relative_to(inbox) or not path.is_file():
            raise ImportValidationError("导入文件不存在或不在指定目录内")
        stat = path.stat()
        if stat.st_size != payload["size"] or stat.st_mtime_ns != payload["mtime_ns"]:
            raise ImportValidationError("文件在排队期间发生变化，请重新提交")
        checksum = file_hash(path)
        batch, count = [], 0
        for sequence, record in records(path, settings.import_batch_size):
            batch.append([dataset_id, sequence, *record])
            count = sequence
            if len(batch) >= settings.import_batch_size:
                analytics.insert(batch)
                batch = []
                heartbeat(count)
        if batch:
            analytics.insert(batch)
        if not count:
            raise ImportValidationError("文件没有行为记录")
        if path.stat().st_mtime_ns != stat.st_mtime_ns or path.stat().st_size != stat.st_size:
            raise ImportValidationError("文件在读取期间发生变化，请重新提交")
        heartbeat(count)
        summary = analytics.summarize(dataset_id, heartbeat)
        with db_factory() as db:
            dataset, job = db.get(Dataset, dataset_id), db.get(Job, job_id)
            if job.status != "running":
                raise ImportValidationError("任务已被结束，不发布此版本")
            dataset.status, dataset.row_count, dataset.sha256 = "ready", count, checksum
            dataset.start_ms, dataset.end_ms, dataset.summary = summary["start_ms"], summary["end_ms"], summary
            setting = db.scalar(select(AppSetting).where(AppSetting.key == "active_dataset").with_for_update())
            if payload["activate"] and setting and setting.value == payload["previous_active"]:
                setting.value = dataset_id
            job.status, job.processed_rows, job.updated_at = "ready", count, now()
            audit(db, actor, "数据管理", "导入完成", f"数据集 {dataset_id}；{count} 条；原样保留重复事件")
            db.commit()
    except Exception as exc:
        logger.exception("Job failed: %s", job_id)
        error = str(exc) if isinstance(exc, ImportValidationError) else "数据库或文件处理失败，请查看本地 worker 日志"
        with db_factory() as db:
            job = db.get(Job, job_id)
            job.status, job.error, job.updated_at = "failed", error[:1000], now()
            if kind == "import":
                db.get(Dataset, dataset_id).status = "failed"
            audit(db, actor, "数据管理", "任务失败", f"{job_id}；{error}", "失败")
            db.commit()


def export_file(job_id, dataset_id, payload, db_factory, analytics, settings, heartbeat):
    # Exports are bounded; training consumes original Parquet, not web downloads.
    root = settings.instance_dir / "exports"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{job_id}.csv"
    p = {"dataset": dataset_id}
    where = "dataset_id={dataset:String}"
    for key, clause in [
        ("keyword", "(user_id={keyword:String} OR item_id={keyword:String})"),
        ("behavior", "behavior_type={behavior:String}"),
        ("from", "event_date>={from:Date}"),
        ("to", "event_date<={to:Date}"),
    ]:
        if payload.get(key):
            p[key] = payload[key]
            where += " AND " + clause
    count = 0
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as target, analytics.client() as client:
            writer = csv.writer(target)
            writer.writerow(["user_id", "item_id", "category_id", "behavior_type", "timestamp"])
            with client.query_rows_stream(
                f"SELECT user_id,item_id,category_id,behavior_type,event_time_ms FROM behavior_events WHERE {where} ORDER BY event_date,user_id,event_time_ms,sequence LIMIT 1000001",
                parameters=p,
            ) as stream:
                for row in stream:
                    count += 1
                    if count > 1000000:
                        raise ImportValidationError("导出超过 100 万条，请缩小筛选范围；训练请使用原始数据文件")
                    writer.writerow([("'" + str(v)) if str(v).startswith(("=", "+", "-", "@")) else v for v in row])
                    if count % 10000 == 0:
                        heartbeat(count)
        with db_factory() as db:
            job = db.get(Job, job_id)
            if job.status != "running":
                raise ImportValidationError("任务已被结束，不发布导出文件")
            job.status, job.processed_rows, job.updated_at = "ready", count, now()
            audit(db, job.created_by, "数据管理", "导出完成", f"{job_id}；{count} 条")
            db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    database, analytics = Database(settings), Analytics(settings)
    logger.info("Worker ready (one durable job at a time)")
    while True:
        try:
            job_id = claim(database.session)
            if job_id:
                run_job(job_id, database.session, analytics, settings)
            else:
                time.sleep(1)
        except KeyboardInterrupt:
            return
        except Exception:
            logger.exception("Queue unavailable; retrying")
            time.sleep(5)


if __name__ == "__main__":
    main()
