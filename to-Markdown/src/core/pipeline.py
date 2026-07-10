"""
编排器 —— 防腐层第3层

职责：
1. 接收校验后的 ConvertRequest
2. 管理任务生命周期（状态机驱动）
3. 调度具体 processor 执行
4. 统一错误处理与结果封装
5. 批量处理并发编排

设计要点：
- Pipeline 不包含业务逻辑——只做编排
- 每个 processor 独立运行，通过 InternalTask 通信
- 批量处理时，单个失败不影响其他
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ..schemas.enums import TaskState, ErrorCategory
from ..schemas.input import ConvertRequest
from ..schemas.output import Result, TaskError, NoteOutput, BatchResult
from ..schemas.task import InternalTask
from .state_machine import StateMachine
from .checkpoint import CheckpointManager


class Pipeline:
    """处理管线编排器"""

    def __init__(
        self,
        checkpoint_dir: Path | None = None,
        max_concurrency: int = 4,
        retention_days: int = 30,
    ):
        self.checkpoint = CheckpointManager(checkpoint_dir, retention_days=retention_days)
        self.max_concurrency = max_concurrency
        self._cleanup_counter = 0  # 每 N 次执行触发一次过期清理

    async def execute(self, request: ConvertRequest) -> Result[NoteOutput]:
        """执行单个转换请求

        流程：
        1. 创建 InternalTask
        2. 检查 checkpoint（幂等性）
        3. 状态机驱动执行
        4. finally 块确保状态正确终结（消除拦截盲区）
        5. 返回统一 Result
        """
        task = InternalTask.from_request(request)
        result: Optional[Result[NoteOutput]] = None

        # 幂等性检查
        if not request.force:
            cached = self._check_cached_result(request)
            if cached is not None:
                return cached

        # 状态转换：IDLE → VALIDATED → RUNNING
        try:
            StateMachine.transition(task, TaskState.VALIDATED, "提交校验")
            StateMachine.transition(task, TaskState.RUNNING, "开始处理")
        except ValueError as e:
            return Result.failure(TaskError(
                code="STATE_TRANSITION_ERROR",
                message=str(e),
                category=ErrorCategory.NON_RETRYABLE,
            ))

        try:
            # ── 执行处理（异常上浮到此层统一捕获）──
            result = await self._dispatch(task, request)
            # 成功路径：标记完成
            if result.is_success and result.data:
                self.checkpoint.mark_completed(
                    task.task_type, task.input_hash,
                    result.data.output_path,
                    {"elapsed_seconds": task.elapsed_seconds},
                )
                StateMachine.transition(task, TaskState.COMPLETED, "处理完成")
            elif result.is_failure:
                self.checkpoint.mark_failed(
                    task.task_type, task.input_hash,
                    result.errors[0].message if result.errors else "未知错误",
                    task.completed_chunk_indices,
                )
                StateMachine.transition(task, TaskState.FAILED, "处理器返回失败")
            return result

        except Exception as e:
            # ── 异常路径：统一标记失败 + 推进状态机 ──
            self.checkpoint.mark_failed(
                task.task_type, task.input_hash, str(e),
                task.completed_chunk_indices,
            )
            try:
                StateMachine.transition(task, TaskState.FAILED, str(e)[:200])
            except ValueError:
                pass  # 状态机可能已处于终态，忽略转换错误
            return Result.failure(TaskError(
                code="PROCESSING_ERROR",
                message=str(e),
                category=ErrorCategory.RETRYABLE,
            ))
        finally:
            # ── finally 保障：无论正常/异常/处理器内部返回失败，状态机必定被推进 ──
            if task.state not in (TaskState.COMPLETED, TaskState.FAILED):
                try:
                    self.checkpoint.mark_failed(
                        task.task_type, task.input_hash,
                        "处理异常终止（finally 兜底）",
                        task.completed_chunk_indices,
                    )
                    StateMachine.transition(task, TaskState.FAILED, "finally 兜底")
                except Exception:
                    pass  # 尽最大努力标记，不抛出新异常
            # 每 10 次执行触发一次过期 checkpoint 清理
            self._cleanup_counter += 1
            if self._cleanup_counter % 10 == 0:
                try:
                    self.checkpoint.cleanup_expired()
                except Exception:
                    pass

    async def execute_batch(
        self,
        requests: list[ConvertRequest],
    ) -> BatchResult:
        """批量执行（并发控制）"""
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def bounded_execute(req: ConvertRequest) -> Result[NoteOutput]:
            async with semaphore:
                return await self.execute(req)

        tasks_coros = [bounded_execute(r) for r in requests]
        results = await asyncio.gather(*tasks_coros, return_exceptions=True)

        batch = BatchResult(
            total=len(requests),
            completed=0,
            failed=0,
            skipped=0,
            results=[],
        )

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                batch.failed += 1
                batch.results.append(Result.failure(TaskError(
                    code="BATCH_ERROR",
                    message=str(r),
                    category=ErrorCategory.RETRYABLE,
                )))
            elif r.is_success:
                batch.completed += 1
                batch.results.append(r)
            elif r.is_failure:
                batch.failed += 1
                batch.results.append(r)

        return batch

    async def _dispatch(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """分发到具体 processor（工厂注入共享 CheckpointManager）"""
        from ..processors import ProcessorFactory

        factory = ProcessorFactory(checkpoint_manager=self.checkpoint)
        try:
            processor = factory.create(request.task_type)
        except ValueError as e:
            return Result.failure(TaskError(
                code="UNSUPPORTED_TASK_TYPE",
                message=str(e),
                category=ErrorCategory.NON_RETRYABLE,
            ))

        return await processor.process(task, request)

    def _check_cached_result(self, request: ConvertRequest) -> Optional[Result[NoteOutput]]:
        """检查是否有缓存的完成结果"""
        input_hash = request.compute_input_hash()
        data = self.checkpoint.load(request.task_type, input_hash)
        if data is None:
            return None

        if data.get("state") != TaskState.COMPLETED.value:
            return None

        output_path = data.get("output_path")
        if not output_path or not Path(output_path).exists():
            return None

        # 返回缓存结果
        path = Path(output_path)
        note = NoteOutput(
            title=path.stem,
            content=path.read_text(encoding="utf-8") if path.exists() else "",
            output_path=path,
            source_info=data.get("metadata", {}),
            processing_time_seconds=0.0,
        )
        return Result.success(note, cached=True)
