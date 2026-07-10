"""
输出 Schema —— 防腐层第4层

统一的输出标准：
- 成功: Result.success(data)
- 失败: Result.failure(error)
- 部分成功: Result.partial(data, errors)

所有模块输出必须通过此标准封装，杜绝"抛异常=失败"的混乱。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, TypeVar, Optional

from .enums import TaskState, ErrorCategory

T = TypeVar("T")


@dataclass
class TaskError:
    """结构化错误信息"""
    code: str
    message: str
    category: ErrorCategory
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category.value,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Result(Generic[T]):
    """统一结果封装

    使用方式：
        Result.success(data)       → 完全成功
        Result.failure(error)      → 完全失败
        Result.partial(data, errors) → 部分成功
    """
    state: TaskState
    data: Optional[T] = None
    errors: list[TaskError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def success(cls, data: T, **metadata) -> "Result[T]":
        return cls(state=TaskState.COMPLETED, data=data, metadata=metadata)

    @classmethod
    def failure(cls, error: TaskError | str, category: ErrorCategory = ErrorCategory.NON_RETRYABLE) -> "Result[T]":
        if isinstance(error, str):
            error = TaskError(
                code="UNKNOWN_ERROR",
                message=error,
                category=category,
            )
        return cls(state=TaskState.FAILED, errors=[error])

    @classmethod
    def partial(cls, data: T, errors: list[TaskError], warnings: list[str] | None = None) -> "Result[T]":
        return cls(
            state=TaskState.COMPLETED,
            data=data,
            errors=errors,
            warnings=warnings or [],
        )

    @property
    def is_success(self) -> bool:
        return self.state == TaskState.COMPLETED and not self.errors

    @property
    def is_failure(self) -> bool:
        return self.state == TaskState.FAILED

    @property
    def is_partial(self) -> bool:
        return self.state == TaskState.COMPLETED and len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


@dataclass
class ChunkResult:
    """单个分块的处理结果"""
    index: int
    page_range: tuple[int, int]
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class NoteOutput:
    """最终笔记输出"""
    title: str
    content: str
    output_path: Path
    source_info: dict = field(default_factory=dict)
    chunk_count: int = 0
    total_pages: int = 0
    processing_time_seconds: float = 0.0

    def validate(self) -> list[str]:
        """写入磁盘前的校验，返回问题列表（空列表=通过）"""
        issues = []
        if not self.title or not self.title.strip():
            issues.append("title 为空")
        if not self.content or not self.content.strip():
            issues.append("content 为空")
        if str(self.output_path) == ".":
            issues.append("output_path 未设置（仍为默认值 '.'）")
        return issues

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    def to_markdown(self) -> str:
        """生成最终 Markdown 内容"""
        lines = [
            f"# {self.title}",
            "",
            f"> 来源: {self.source_info.get('source', 'N/A')}",
            f"> 处理时间: {self.processing_time_seconds:.1f}s",
            f"> 页数: {self.total_pages}",
            "",
            "---",
            "",
            self.content,
        ]
        return "\n".join(lines)

    def to_obsidian(self) -> str:
        """生成 Obsidian 格式（含 frontmatter）"""
        import yaml
        frontmatter = {
            "title": self.title,
            "source": self.source_info.get("source", "N/A"),
            "source_type": self.source_info.get("type", "unknown"),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "tags": self.source_info.get("tags", []),
            "pages": self.total_pages,
        }
        lines = [
            "---",
            yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip(),
            "---",
            "",
            f"# {self.title}",
            "",
            self.content,
        ]
        return "\n".join(lines)


@dataclass
class BatchResult:
    """批量处理结果"""
    total: int
    completed: int
    failed: int
    skipped: int  # 因断点续传跳过的
    results: list[Result[NoteOutput]]
    summary: str = ""

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return (self.completed + self.skipped) / self.total
