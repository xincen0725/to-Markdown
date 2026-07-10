"""
重试引擎 + 熔断器

设计要点：
1. 指数退避 + 随机抖动（防止惊群效应）
2. 全局超时熔断——防止单个调用拖垮整个技能
3. 错误分类驱动重试决策（retryable vs non-retryable）
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Callable, Awaitable, TypeVar, Optional

from ..schemas.enums import ErrorCategory
from ..schemas.output import TaskError

T = TypeVar("T")


@dataclass
class RetryStats:
    """重试统计"""
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    total_retry_delay: float = 0.0


class CircuitBreaker:
    """熔断器

    当连续失败达到阈值时，熔断器打开，拒绝所有请求。
    经过冷却时间后，进入半开状态，允许探测请求。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        half_open_max: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max = half_open_max

        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"  # closed, open, half_open
        self._half_open_attempts = 0

    @property
    def is_open(self) -> bool:
        if self._state == "closed":
            return False
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "half_open"
                self._half_open_attempts = 0
                return False
            return True
        # half_open
        return self._half_open_attempts >= self.half_open_max

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "half_open":
            self._half_open_attempts += 1
        if self._failure_count >= self.failure_threshold:
            self._state = "open"


class RetryEngine:
    """重试引擎

    退避策略：指数退避 + 随机抖动
    delay = min(base_delay * 2^attempt + jitter, max_delay)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        timeout: float = 300.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.stats = RetryStats()
        self.circuit_breaker = CircuitBreaker()

    def _compute_delay(self, attempt: int) -> float:
        """计算退避延迟"""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        jitter = random.uniform(0, delay * 0.5)
        return delay + jitter

    async def execute(
        self,
        func: Callable[..., Awaitable[T]],
        *args,
        error_classifier: Callable[[Exception], ErrorCategory] | None = None,
        **kwargs,
    ) -> T:
        """执行带重试的函数

        Args:
            func: 异步函数
            error_classifier: 错误分类器，返回 ErrorCategory
            *args, **kwargs: 传递给 func

        Returns:
            func 的返回值

        Raises:
            TimeoutError: 全局超时
            RuntimeError: 熔断器打开
            Exception: 重试耗尽后抛出最后一次异常
        """
        if self.circuit_breaker.is_open:
            raise RuntimeError("熔断器已打开，拒绝执行")

        last_exception: Optional[Exception] = None
        start_time = time.monotonic()

        # 边界防御：确保至少执行1次
        effective_retries = max(0, self.max_retries)

        for attempt in range(effective_retries + 1):
            # 检查全局超时
            elapsed = time.monotonic() - start_time
            if elapsed > self.timeout:
                raise TimeoutError(f"全局超时 ({self.timeout}s)，已执行 {attempt} 次尝试")

            self.stats.total_attempts += 1

            try:
                # 剩余时间限制
                remaining = self.timeout - elapsed
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=remaining,
                )
                self.stats.successful_attempts += 1
                self.circuit_breaker.record_success()
                return result

            except asyncio.TimeoutError:
                last_exception = TimeoutError(f"单次调用超时 ({remaining:.0f}s)")
                self.stats.failed_attempts += 1
                self.circuit_breaker.record_failure()

            except Exception as e:
                last_exception = e
                self.stats.failed_attempts += 1
                self.circuit_breaker.record_failure()

                # 错误分类
                category = ErrorCategory.RETRYABLE  # 默认可重试
                if error_classifier:
                    category = error_classifier(e)

                if category == ErrorCategory.NON_RETRYABLE:
                    raise  # 不可重试，直接抛出

            # 最后一次尝试失败
            if attempt == effective_retries:
                break

            # 退避等待
            delay = self._compute_delay(attempt)
            self.stats.total_retry_delay += delay
            await asyncio.sleep(delay)

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("重试耗尽，但未捕获到异常（逻辑错误）")


def sync_retry(
    func: Callable[..., T] | None = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    error_classifier: Callable[[Exception], ErrorCategory] | None = None,
) -> Callable[..., T]:
    """同步重试——可作为装饰器或直接包装函数

    用法1（装饰器）：
        @sync_retry(max_retries=3)
        def my_func(): ...

    用法2（包装）：
        wrapped = sync_retry(my_func, max_retries=3)
        result = wrapped()
    """
    import functools

    # 边界防御：负值或不合理的重试次数
    _safe_retries = max(0, max_retries)

    def decorator(f: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(f)
        def wrapper(*args, **kwargs) -> T:
            last_exception: Exception | None = None
            for attempt in range(_safe_retries + 1):
                try:
                    return f(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    category = ErrorCategory.RETRYABLE
                    if error_classifier:
                        category = error_classifier(e)
                    if category == ErrorCategory.NON_RETRYABLE:
                        raise
                    if attempt < _safe_retries:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        delay += random.uniform(0, delay * 0.5)
                        time.sleep(delay)
            if last_exception is not None:
                raise last_exception
            raise RuntimeError("重试耗尽，但未捕获到异常（逻辑错误）")
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
