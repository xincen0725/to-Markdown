"""
结构化日志模块

统一替换全局 print()，提供：
1. 按模块过滤（logger name = 模块路径）
2. 按级别过滤（DEBUG/INFO/WARNING/ERROR）
3. 结构化上下文字段
"""
from __future__ import annotations

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """获取结构化日志器

    Usage:
        logger = get_logger(__name__)
        logger.info("开始处理", extra={"task_id": task_id})
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            fmt="[%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
