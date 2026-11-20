"""路径冲突检查与原子写入。

审查发现：如果输出路径误指向输入文件，旧实现会先删除输出，导致原始数据被破坏。
这里在任何 mkdir/unlink/写文件之前统一检查，并让 Parquet/JSON/Markdown 先写临时文件，
成功后再替换最终文件；失败时保留上一次可用输出。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Iterator


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _normalized(path: str | Path) -> str:
    return os.path.normcase(str(_resolved(path)))


def same_path(left: str | Path, right: str | Path) -> bool:
    """判断两个路径是否指向同一文件。

    - 先比较规范化后的绝对路径（处理 ``..``、大小写、符号链接）；
    - 两边都存在时再用 ``os.path.samefile`` 处理硬链接/别名。
    """
    if _normalized(left) == _normalized(right):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def ensure_distinct_paths(
    inputs: Iterable[str | Path],
    outputs: Iterable[tuple[str, str | Path | None]],
) -> None:
    """检查输入、输出、报告路径是否互相冲突。

    ``outputs`` 是 ``(名称, 路径)`` 列表；名称为空或路径为 None 的项忽略。
    必须在创建目录、删除旧文件或打开 writer 之前调用。
    """
    input_paths = [Path(p) for p in inputs]
    named_outputs = [
        (name, Path(path))
        for name, path in outputs
        if path is not None and str(path).strip()
    ]

    for index, left in enumerate(input_paths):
        for right in input_paths[index + 1 :]:
            if same_path(left, right):
                raise ValueError(
                    f"输入文件重复：{left} 与 {right} 指向同一文件"
                )

    for input_path in input_paths:
        for name, output_path in named_outputs:
            if same_path(input_path, output_path):
                raise ValueError(
                    f"{name} 路径与输入文件相同：{output_path}；"
                    "为防止破坏原始数据，已拒绝执行"
                )

    for index, (name_a, path_a) in enumerate(named_outputs):
        for name_b, path_b in named_outputs[index + 1 :]:
            if same_path(path_a, path_b):
                raise ValueError(
                    f"{name_a} 与 {name_b} 指向同一文件：{path_a}"
                )


@contextlib.contextmanager
def atomic_output(final_path: str | Path) -> Iterator[Path]:
    """在最终路径同目录下写临时文件，成功后再原子替换。

    用法::

        with atomic_output(final) as tmp:
            write_to(tmp)
        # 只有 write_to 成功才会替换 final；异常时 final 保持原样
    """
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{final.name}.", dir=final.parent)
    )
    temp_path = temp_dir / final.name
    try:
        yield temp_path
        os.replace(temp_path, final)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
