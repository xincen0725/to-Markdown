"""
断点续传引擎 —— 幂等性核心

设计要点：
1. 基于输入 SHA256 的 checkpoint 文件
2. 支持分块级断点续传
3. 文件锁防止并发写入（跨平台兼容）
4. checkpoint 包含输入快照，防篡改检测
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .logging import get_logger
from ..schemas.enums import TaskState, TaskType

_logger = get_logger(__name__)

# 跨平台文件锁
if sys.platform == "win32":
    import msvcrt

    def _lock_file_exclusive(f):
        """Windows 排他锁"""
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _lock_file_shared(f):
        """Windows 共享锁"""
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(f):
        """Windows 解锁"""
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_file_exclusive(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _lock_file_shared(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class CheckpointManager:
    """断点续传管理器

    checkpoint 目录结构：
    .checkpoints/
    ├── pdf_to_note/
    │   ├── {input_hash}.json
    │   └── ...
    ├── sop_extract/
    ├── video_to_note/
    ├── audio_to_note/
    └── web_to_note/
    """

    # Checkpoint 数据格式版本——升级时递增，兼容旧格式
    SCHEMA_VERSION = 1
    # 默认保留天数，超过自动清理
    DEFAULT_RETENTION_DAYS = 30

    def __init__(self, base_dir: Path | None = None, retention_days: int = DEFAULT_RETENTION_DAYS):
        self.base_dir = (base_dir or Path.cwd() / ".checkpoints").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(1, retention_days)

    def _get_checkpoint_path(self, task_type: TaskType, input_hash: str) -> Path:
        """获取 checkpoint 文件路径"""
        subdir = self.base_dir / task_type.value
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{input_hash}.json"

    def exists(self, task_type: TaskType, input_hash: str) -> bool:
        """检查 checkpoint 是否存在"""
        return self._get_checkpoint_path(task_type, input_hash).exists()

    def load(self, task_type: TaskType, input_hash: str) -> Optional[dict]:
        """加载 checkpoint

        Returns:
            checkpoint 数据字典，不存在返回 None
        """
        path = self._get_checkpoint_path(task_type, input_hash)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                _lock_file_shared(f)
                try:
                    data = json.load(f)
                finally:
                    _unlock_file(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("读取失败，将重新处理: %s", path, extra={"error": str(e)})
            return None

    def save(self, task_type: TaskType, input_hash: str, data: dict) -> None:
        """保存 checkpoint（原子写入）

        先写临时文件，再 rename，保证原子性。
        自动注入 schema_version 确保格式向前兼容。
        """
        path = self._get_checkpoint_path(task_type, input_hash)
        tmp_path = path.with_suffix(".tmp")

        # 确保数据包含必要字段
        data.setdefault("input_hash", input_hash)
        data.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        data.setdefault("schema_version", self.SCHEMA_VERSION)

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                _lock_file_exclusive(f)
                try:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    _unlock_file(f)

            # 原子替换
            tmp_path.replace(path)
        except OSError as e:
            tmp_path.unlink(missing_ok=True)
            raise OSError(f"保存 checkpoint 失败: {e}")

    def update_chunks(
        self,
        task_type: TaskType,
        input_hash: str,
        completed_indices: set[int],
    ) -> None:
        """更新已完成的 chunk 列表（增量更新）"""
        data = self.load(task_type, input_hash) or {}
        data["chunks_completed"] = sorted(list(completed_indices))
        self.save(task_type, input_hash, data)

    def get_completed_chunks(
        self,
        task_type: TaskType,
        input_hash: str,
    ) -> set[int]:
        """获取已完成的 chunk 索引"""
        data = self.load(task_type, input_hash)
        if data is None:
            return set()
        return set(data.get("chunks_completed", []))

    def is_fully_completed(
        self,
        task_type: TaskType,
        input_hash: str,
        total_chunks: int,
    ) -> bool:
        """检查是否所有 chunk 都已完成"""
        completed = self.get_completed_chunks(task_type, input_hash)
        return len(completed) >= total_chunks

    def mark_completed(
        self,
        task_type: TaskType,
        input_hash: str,
        output_path: Path,
        metadata: dict | None = None,
    ) -> None:
        """标记任务为完成"""
        data = {
            "input_hash": input_hash,
            "state": TaskState.COMPLETED.value,
            "output_path": str(output_path),
            "output_hash": self._file_sha256(output_path) if output_path.exists() else "",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        self.save(task_type, input_hash, data)

    def mark_failed(
        self,
        task_type: TaskType,
        input_hash: str,
        error_message: str,
        chunks_completed: set[int] | None = None,
    ) -> None:
        """标记任务为失败"""
        data = {
            "input_hash": input_hash,
            "state": TaskState.FAILED.value,
            "error": error_message,
            "chunks_completed": sorted(list(chunks_completed or [])),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save(task_type, input_hash, data)

    def delete(self, task_type: TaskType, input_hash: str) -> None:
        """删除 checkpoint（用于 --force 强制重新处理）"""
        path = self._get_checkpoint_path(task_type, input_hash)
        path.unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        """清理超过 retention_days 的过期 checkpoint 文件

        遍历所有任务类型子目录，删除更新时间超过保留期的文件。
        返回删除的文件数量。
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (self.retention_days * 86400)
        deleted = 0
        for subdir in self.base_dir.iterdir():
            if not subdir.is_dir():
                continue
            for cp_file in subdir.glob("*.json"):
                try:
                    mtime = cp_file.stat().st_mtime
                    if mtime < cutoff:
                        cp_file.unlink(missing_ok=True)
                        deleted += 1
                except OSError:
                    pass
        if deleted:
            _logger.info("清理了 %d 个过期 checkpoint（保留期 %d 天）", deleted, self.retention_days)
        return deleted

    def verify_integrity(
        self,
        task_type: TaskType,
        input_hash: str,
        expected_input_params: dict,
    ) -> bool:
        """校验 checkpoint 完整性

        检查：
        1. checkpoint 是否存在
        2. 状态是否为 COMPLETED
        3. 输出文件是否存在（跨平台兼容）
        4. 输入参数是否一致（防篡改）
        """
        data = self.load(task_type, input_hash)
        if data is None:
            return False

        if data.get("state") != TaskState.COMPLETED.value:
            return False

        output_path_str = data.get("output_path")
        if not output_path_str:
            return False
        # 跨平台路径兼容：将存储的路径转为当前系统的 Path
        output_path = Path(output_path_str)
        if not output_path.exists():
            return False

        # 可选：验证输入参数一致性
        stored_params = data.get("input_params")
        if stored_params and stored_params != expected_input_params:
            return False

        return True

    @staticmethod
    def _file_sha256(path: Path) -> str:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
