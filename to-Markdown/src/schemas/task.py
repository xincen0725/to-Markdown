"""
内部任务模型 —— 模块间传递的标准数据结构

这是防腐层第3层的核心：所有 processor 只接受和返回 InternalTask/InternalChunk，
杜绝模块间直接传递原始数据导致的格式污染。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .enums import TaskState, TaskType
from .input import ConvertRequest, InputUnion
from .contracts import ChunkMetadata


@dataclass
class InternalChunk:
    """标准化的数据块——模块间唯一通信格式"""
    index: int
    page_range: tuple[int, int]
    raw_text: str = ""
    ocr_text: str = ""
    metadata: ChunkMetadata = field(default_factory=dict)  # type: ignore[assignment]

    @property
    def combined_text(self) -> str:
        """优先 OCR 文本，否则使用原始文本"""
        return self.ocr_text or self.raw_text


@dataclass
class InternalTask:
    """内部任务表示——经过防腐层校验后的标准化任务"""
    task_id: str  # = input_hash
    task_type: TaskType
    input: InputUnion
    input_hash: str
    output_dir: Path
    force: bool

    # 状态追踪
    state: TaskState = TaskState.IDLE
    state_history: list[tuple[TaskState, datetime]] = field(default_factory=list)

    # 处理中间数据
    chunks: list[InternalChunk] = field(default_factory=list)
    completed_chunk_indices: set[int] = field(default_factory=set)

    # 时间追踪
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # 输出
    output_path: Optional[Path] = None

    @classmethod
    def from_request(cls, request: ConvertRequest) -> "InternalTask":
        """从 ConvertRequest 创建 InternalTask（防腐层转换）"""
        return cls(
            task_id=request.compute_input_hash(),
            task_type=request.task_type,
            input=request.input,
            input_hash=request.compute_input_hash(),
            output_dir=request.output.output_dir,
            force=request.force,
        )

    def transition_to(self, new_state: TaskState) -> None:
        """状态转换（单写者模式）"""
        allowed = _ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"非法状态转换: {self.state.value} → {new_state.value}，"
                f"允许: {[s.value for s in allowed]}"
            )
        self.state_history.append((self.state, datetime.now(timezone.utc)))
        self.state = new_state
        if new_state == TaskState.RUNNING and self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        if new_state.is_terminal:
            self.completed_at = datetime.now(timezone.utc)

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()


# 状态转换规则表（单点真理）
_ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.IDLE: {TaskState.VALIDATED},
    TaskState.VALIDATED: {TaskState.RUNNING, TaskState.FAILED},
    TaskState.RUNNING: {TaskState.CHUNKING, TaskState.FAILED, TaskState.PAUSED},
    TaskState.CHUNKING: {TaskState.MERGING, TaskState.FAILED},
    TaskState.MERGING: {TaskState.COMPLETED, TaskState.FAILED},
    TaskState.PAUSED: {TaskState.RUNNING},
    TaskState.FAILED: {TaskState.RUNNING},  # 重试
    TaskState.COMPLETED: set(),  # 终态，不可转换
}
