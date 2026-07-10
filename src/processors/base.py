"""
处理器抽象基类

所有 processor 必须继承此类，保证：
1. 统一的接口签名
2. 统一的错误处理
3. 通过 InternalTask/InternalChunk 通信——不直接访问外部数据
4. 公共方法提取——_load_cached_result/_save_output 消除 5 处重复
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from ..schemas.enums import ErrorCategory
from ..schemas.input import ConvertRequest, OutputConfig
from ..schemas.output import Result, TaskError, NoteOutput
from ..schemas.task import InternalTask


class BaseProcessor(ABC):
    """处理器基类"""

    # 处理器名称（用于日志）
    name: str = "base"

    @abstractmethod
    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """处理入口"""
        ...

    def validate_input(self, task: InternalTask) -> Result[None]:
        """处理器级输入校验（可选覆盖）"""
        return Result.success(None)

    def cleanup(self, task: InternalTask) -> None:
        """清理资源（可选覆盖）"""
        pass

    # ─── 公共方法（消除子类重复）───

    def _load_cached_result(self, cached_data: dict) -> Result[NoteOutput]:
        """从 checkpoint 缓存加载结果（统一实现）"""
        output_path = Path(cached_data["output_path"])
        if not output_path.exists():
            return Result.failure(TaskError(
                code="CACHE_MISSING",
                message=f"缓存输出文件不存在: {output_path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))
        note = NoteOutput(
            title=output_path.stem,
            content=output_path.read_text(encoding="utf-8"),
            output_path=output_path,
            source_info=cached_data.get("metadata", {}),
            processing_time_seconds=0.0,
        )
        return Result.success(note, cached=True)

    def _save_output(self, note: NoteOutput, output_config: OutputConfig) -> Path:
        """保存输出文件到磁盘（统一实现）

        Returns:
            实际保存的文件路径
        """
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', note.title)[:100]
        output_path = output_config.output_dir / f"{safe_title}.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        content = note.to_markdown()
        if output_config.format.value == "obsidian":
            content = note.to_obsidian()

        output_path.write_text(content, encoding="utf-8")
        return output_path
