"""
任务上下文容器

替代 InternalTask 中散落的可变状态管理。
TaskContext 是所有可变状态的唯一写入入口，确保：
1. chunk 进度原子更新
2. 状态转换经过校验
3. 外部只能通过 TaskContext 观察/修改任务状态

设计要点：
- 不可变字段通过 InternalTask 携带（task_id, task_type, input_hash）
- 可变状态通过 TaskContext 管理（state, chunks, completed_indices, timestamps）
- Processor 不直接操作 CheckpointManager——通过 TaskContext 间接操作
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..schemas.enums import TaskState
from ..schemas.task import InternalTask, InternalChunk


class TaskContext:
    """任务上下文——可变状态的唯一管理入口"""

    def __init__(self, task: InternalTask):
        self._task = task

    # ─── 只读属性 ───

    @property
    def task_id(self) -> str:
        return self._task.task_id

    @property
    def task_type(self):
        return self._task.task_type

    @property
    def input_hash(self) -> str:
        return self._task.input_hash

    @property
    def state(self) -> TaskState:
        return self._task.state

    @property
    def output_dir(self) -> Path:
        return self._task.output_dir

    @property
    def force(self) -> bool:
        return self._task.force

    @property
    def started_at(self) -> Optional[datetime]:
        return self._task.started_at

    @property
    def elapsed_seconds(self) -> float:
        return self._task.elapsed_seconds

    # ─── 可变状态操作 ───

    @property
    def chunks(self) -> list[InternalChunk]:
        return self._task.chunks

    @chunks.setter
    def chunks(self, value: list[InternalChunk]) -> None:
        self._task.chunks = value

    @property
    def completed_chunk_indices(self) -> set[int]:
        return self._task.completed_chunk_indices

    def mark_chunk_completed(self, index: int) -> None:
        """标记一个 chunk 已完成（原子操作）"""
        self._task.completed_chunk_indices.add(index)

    def transition_state(self, new_state: TaskState, reason: str = "") -> None:
        """状态转换（委托给 InternalTask 的校验逻辑）"""
        self._task.transition_to(new_state)
        # 日志由 StateMachine 层输出

    @property
    def output_path(self) -> Optional[Path]:
        return self._task.output_path

    @output_path.setter
    def output_path(self, value: Path) -> None:
        self._task.output_path = value

    @property
    def is_completed(self) -> bool:
        return self._task.state == TaskState.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self._task.state == TaskState.FAILED

    @property
    def is_terminal(self) -> bool:
        return self._task.state.is_terminal
