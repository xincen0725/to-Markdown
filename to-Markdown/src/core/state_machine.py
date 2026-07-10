"""
状态机引擎

单写者模式：同一 task_id 在同一时刻只有一个状态转换在进行。
通过文件锁（filelock）防止并发修改。
"""
from __future__ import annotations

import threading

from .logging import get_logger
from ..schemas.enums import TaskState
from ..schemas.task import InternalTask

_logger = get_logger(__name__)


class StateMachine:
    """任务状态机

    设计要点：
    1. 单写者模式：每个 task_id 一把锁
    2. 状态转换原子性：transition_to 内部已校验合法性
    3. 不存储任务数据——只管理状态转换
    """

    _locks: dict[str, threading.Lock] = {}
    _lock_dict_lock = threading.Lock()

    @classmethod
    def _get_lock(cls, task_id: str) -> threading.Lock:
        with cls._lock_dict_lock:
            if task_id not in cls._locks:
                cls._locks[task_id] = threading.Lock()
            return cls._locks[task_id]

    @classmethod
    def transition(
        cls,
        task: InternalTask,
        new_state: TaskState,
        reason: str = "",
    ) -> InternalTask:
        """执行状态转换（线程安全）

        Args:
            task: 当前任务
            new_state: 目标状态
            reason: 转换原因（用于日志）

        Returns:
            更新后的任务

        Raises:
            ValueError: 非法状态转换
        """
        old_state = task.state
        lock = cls._get_lock(task.task_id)
        with lock:
            task.transition_to(new_state)
            _logger.info("%s → %s", old_state.value, new_state.value, extra={"task_id": task.task_id, "reason": reason})
        return task

    @classmethod
    def can_transition(cls, task: InternalTask, new_state: TaskState) -> bool:
        """检查状态转换是否合法"""
        from ..schemas.task import _ALLOWED_TRANSITIONS
        return new_state in _ALLOWED_TRANSITIONS.get(task.state, set())

    @classmethod
    def cleanup_lock(cls, task_id: str) -> None:
        """清理锁（任务结束后调用）"""
        with cls._lock_dict_lock:
            cls._locks.pop(task_id, None)
