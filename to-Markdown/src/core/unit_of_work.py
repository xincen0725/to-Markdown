"""
Unit of Work —— 事务边界管理器

所有副作用操作（文件写入、checkpoint 更新、API 调用）必须通过 UoW 执行。

设计要点：
1. 正向操作 + 回滚操作成对注册
2. commit() → 清空回滚栈，确认持久化
3. rollback() → 逆序执行回滚操作，撤销所有副作用
4. 幂等性：commit 后不再回滚；rollback 后不再提交

使用方式：
    uow = UnitOfWork(checkpoint)
    uow.register(
        forward=lambda: write_file(path, content),
        rollback=lambda: path.unlink(),
    )
    uow.checkpoint_update(task_type, hash, indices)
    uow.commit()  # 或 uow.rollback()
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .logging import get_logger
from .checkpoint import CheckpointManager
from ..schemas.enums import TaskType

_logger = get_logger(__name__)


class UnitOfWork:
    """事务边界管理器"""

    def __init__(self, checkpoint: CheckpointManager):
        self._checkpoint = checkpoint
        self._rollbacks: list[Callable[[], None]] = []
        self._committed = False
        self._rolled_back = False

    # ─── 副作用注册 ───

    def register(
        self,
        forward: Callable[[], None],
        rollback: Callable[[], None],
    ) -> None:
        """注册一对正向/回滚操作

        正向操作立即执行，回滚操作入栈。
        若正向操作失败，不会入栈回滚操作。
        """
        if self._committed or self._rolled_back:
            raise RuntimeError("事务已终结，无法注册新操作")
        try:
            forward()
            self._rollbacks.append(rollback)
        except Exception:
            # 正向操作失败，不回滚已成功的操作（它们会被外层 finally 处理）
            raise

    def write_file(self, path: Path, content: str) -> None:
        """带事务的文件写入"""
        backup: Optional[str] = None
        existed = path.exists()
        if existed:
            backup = path.read_text(encoding="utf-8")

        def _write():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        def _rollback():
            if existed and backup is not None:
                path.write_text(backup, encoding="utf-8")
            elif not existed:
                path.unlink(missing_ok=True)

        self.register(_write, _rollback)

    def checkpoint_update(
        self, task_type: TaskType, input_hash: str, indices: set[int]
    ) -> None:
        """带事务的 checkpoint chunk 进度更新"""
        old = self._checkpoint.get_completed_chunks(task_type, input_hash)

        def _update():
            self._checkpoint.update_chunks(task_type, input_hash, indices)

        def _rollback():
            self._checkpoint.update_chunks(task_type, input_hash, old)

        self.register(_update, _rollback)

    def checkpoint_mark_completed(
        self, task_type: TaskType, input_hash: str,
        output_path: Path, metadata: Optional[dict] = None,
    ) -> None:
        """带事务的 checkpoint 完成标记"""
        def _mark():
            self._checkpoint.mark_completed(task_type, input_hash, output_path, metadata)

        def _rollback():
            self._checkpoint.delete(task_type, input_hash)

        self.register(_mark, _rollback)

    def checkpoint_mark_failed(
        self, task_type: TaskType, input_hash: str,
        error_message: str, chunks_completed: Optional[set[int]] = None,
    ) -> None:
        """带事务的 checkpoint 失败标记"""
        old_data = self._checkpoint.load(task_type, input_hash)

        def _mark():
            self._checkpoint.mark_failed(task_type, input_hash, error_message, chunks_completed)

        def _rollback():
            if old_data:
                self._checkpoint.save(task_type, input_hash, old_data)
            else:
                self._checkpoint.delete(task_type, input_hash)

        self.register(_mark, _rollback)

    # ─── 事务控制 ───

    def commit(self) -> None:
        """提交事务：清空回滚栈"""
        if self._rolled_back:
            raise RuntimeError("事务已回滚，无法提交")
        self._committed = True
        self._rollbacks.clear()

    def rollback(self) -> None:
        """回滚事务：逆序执行所有回滚操作

        每个回滚操作失败不影响后续回滚。
        """
        if self._committed:
            raise RuntimeError("事务已提交，无法回滚")
        self._rolled_back = True
        errors: list[str] = []
        for rollback_op in reversed(self._rollbacks):
            try:
                rollback_op()
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
        self._rollbacks.clear()
        if errors:
            # 回滚失败是严重问题但不抛异常——外部已经处于异常处理中
            _logger.error("回滚过程中 %d 个操作失败: %s", len(errors), "; ".join(errors))

    @property
    def is_active(self) -> bool:
        """事务是否仍活跃（未提交也未回滚）"""
        return not self._committed and not self._rolled_back
