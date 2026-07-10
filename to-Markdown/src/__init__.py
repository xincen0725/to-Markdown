"""
to-Markdown: 多源输入转结构化 Markdown 笔记

专为知识管理设计的智能笔记工具。
"""
__version__ = "1.0.0"
__author__ = "to-Markdown"

# 轻量导入——避免在基础导入时触发重型依赖
from .schemas.enums import TaskState, TaskType, InputSource, OutputFormat, ErrorCategory, OCRDirection
from .schemas.output import Result, TaskError, NoteOutput, BatchResult

# 重型导入（仅在需要时）
def get_api():
    """延迟导入 ToMarkdown API（避免触发重型依赖）"""
    from .main import ToMarkdown
    return ToMarkdown

__all__ = [
    # Enums
    "TaskState", "TaskType", "InputSource", "OutputFormat", "ErrorCategory", "OCRDirection",
    # Output
    "Result", "TaskError", "NoteOutput", "BatchResult",
    # API
    "get_api",
]
