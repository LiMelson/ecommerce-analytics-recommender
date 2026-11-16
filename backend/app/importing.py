"""Validate a canonical interchange format; never deduplicate or infer missing events."""

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

COLUMNS = {"user_id", "item_id", "category_id", "behavior_type", "timestamp"}
REQUIRED = COLUMNS - {"category_id"}
BEHAVIORS = {"pv", "fav", "cart", "buy"}


class ImportValidationError(ValueError):
    pass


def parse_record(row, line):
    def identifier(key, required=True):
        value = row.get(key, "")
        if value is None and not required:
            return ""
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ImportValidationError(f"第 {line} 条：{key} 必须是字符串或整数")
        value = str(value)
        if (
            (required and not value.strip())
            or value != value.strip()
            or len(value) > 128
            or any(ord(c) < 32 for c in value)
        ):
            raise ImportValidationError(f"第 {line} 条：{key} 为空、过长或含控制字符")
        return value

    user, item = identifier("user_id"), identifier("item_id")
    category = identifier("category_id", False)
    behavior = row.get("behavior_type")
    if behavior not in BEHAVIORS:
        raise ImportValidationError(f"第 {line} 条：behavior_type 只接受 pv/fav/cart/buy，请先由数据组转换")
    raw = str(row.get("timestamp", ""))
    try:
        if re.fullmatch(r"\d{10}", raw):
            ms = int(raw) * 1000
        elif re.fullmatch(r"\d{13}", raw):
            ms = int(raw)
        else:
            date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("timestamp needs timezone")
            ms = int(date.timestamp() * 1000)
        if not 946684800000 <= ms < 4102444800000:
            raise ValueError("timestamp outside 2000–2100")
    except (ValueError, OverflowError):
        raise ImportValidationError(
            f"第 {line} 条：timestamp 须为 10 位秒、13 位毫秒或带时区的 ISO 时间（2000–2100）"
        ) from None
    return [user, item, category, behavior, ms]


def check_columns(columns):
    if len(columns) != len(set(columns)) or not REQUIRED.issubset(columns) or set(columns) - COLUMNS:
        raise ImportValidationError(
            "字段须包含 user_id,item_id,behavior_type,timestamp；category_id 可选，不接受额外或重复字段"
        )


def records(path: Path, batch_size):
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(path)
        check_columns(parquet.schema_arrow.names)
        line = 0
        for batch in parquet.iter_batches(batch_size=batch_size):
            for row in batch.to_pylist():
                line += 1
                yield line, parse_record(row, line)
    else:
        csv.field_size_limit(1024 * 1024)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source, strict=True)
                check_columns(reader.fieldnames or [])
                for line, row in enumerate(reader, 1):
                    if None in row or any(value is None for value in row.values()):
                        raise ImportValidationError(f"第 {line} 条：列数与表头不一致")
                    yield line, parse_record(row, line)
        except (csv.Error, UnicodeDecodeError):
            raise ImportValidationError("CSV 格式无效，请使用 UTF-8 编码和带表头的标准 CSV") from None


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_inbox(settings, relative_path):
    windows_path, posix_path = PureWindowsPath(relative_path), PurePosixPath(relative_path)
    if (
        windows_path.drive
        or windows_path.is_absolute()
        or posix_path.is_absolute()
        or ".." in windows_path.parts
        or ".." in posix_path.parts
        or any(ord(c) < 32 for c in relative_path)
    ):
        raise ImportValidationError("只接受收件目录内的相对文件名，不接受绝对路径或上级目录")
    root = (settings.instance_dir / "inbox").resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {".csv", ".parquet"}:
        raise ImportValidationError("文件必须位于 backend/instance/inbox 内，格式为 .csv 或 .parquet")
    return path


def queue_import(db, actor, path, name="", activate=True, is_demo=False):
    from .models import AppSetting, Dataset, Job, audit

    current = db.get(AppSetting, "active_dataset")
    dataset = Dataset(
        name=name or path.name[:100],
        filename=path.name[:200],
        created_by=actor,
        source="虚构演示样本" if is_demo else "用户导入",
        is_demo=is_demo,
    )
    db.add(dataset)
    db.flush()
    stat = path.stat()
    job = Job(
        kind="import",
        dataset_id=dataset.id,
        created_by=actor,
        payload={
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "activate": activate,
            "previous_active": current.value if current else "",
        },
    )
    db.add(job)
    audit(db, actor, "数据管理", "提交导入任务", f"数据集 {dataset.id}；{dataset.filename}")
    db.commit()
    return dataset, job
