"""
枚举定义模块
所有枚举在此统一定义，避免循环依赖
"""
from enum import Enum, auto


class TaskState(str, Enum):
    """任务状态机状态"""
    IDLE = "idle"
    VALIDATED = "validated"
    RUNNING = "running"
    CHUNKING = "chunking"
    MERGING = "merging"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """是否为终态"""
        return self in (TaskState.COMPLETED, TaskState.FAILED)

    @property
    def is_active(self) -> bool:
        """是否为活跃态（可进行中）"""
        return self in (TaskState.RUNNING, TaskState.CHUNKING, TaskState.MERGING)


class TaskType(str, Enum):
    """任务类型"""
    PDF_TO_NOTE = "pdf_to_note"
    SOP_EXTRACT = "sop_extract"
    VIDEO_TO_NOTE = "video_to_note"
    AUDIO_TO_NOTE = "audio_to_note"
    WEB_TO_NOTE = "web_to_note"


class InputSource(str, Enum):
    """输入来源"""
    FILE = "file"
    FOLDER = "folder"
    URL = "url"
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"


class OutputFormat(str, Enum):
    """输出格式"""
    MARKDOWN = "markdown"
    OBSIDIAN = "obsidian"  # Obsidian 专用格式（含 frontmatter）


class ErrorCategory(str, Enum):
    """错误分类——用于重试策略决策"""
    RETRYABLE = "retryable"       # 可重试：网络超时、临时故障
    NON_RETRYABLE = "non_retryable"  # 不可重试：输入错误、权限不足
    DEGRADED = "degraded"         # 降级：部分成功


class OCRDirection(str, Enum):
    """OCR 文字方向"""
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    AUTO = "auto"  # 自动判断
