"""JSON / Markdown 输出工具。

pandas 与 numpy 的类型不能直接写 JSON，这里统一转换。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .path_utils import atomic_output


def to_native(value: Any) -> Any:
    """把 numpy / pandas 类型转成 Python 原生类型。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_native(v) for v in value]
    if isinstance(value, np.ndarray):
        return [to_native(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if math.isnan(f) else f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NaT:
        return None
    if value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def write_json(obj: Any, path: str | Path) -> Path:
    """写 JSON，自动创建父目录，使用 UTF-8 与缩进。

    先写临时文件再原子替换，失败时保留上一次可用报告。
    """
    p = Path(path)
    with atomic_output(p) as tmp:
        tmp.write_text(
            json.dumps(to_native(obj), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return p


def write_text(text: str, path: str | Path) -> Path:
    """写文本，原子替换，避免失败时留下半个报告。"""
    p = Path(path)
    with atomic_output(p) as tmp:
        tmp.write_text(text, encoding="utf-8")
    return p
