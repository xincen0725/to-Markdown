# 处理器模块——延迟导入以避免触发重型依赖加载
# 使用 ProcessorFactory 工厂统一创建，自动注入共享依赖

from .base import BaseProcessor

__all__ = [
    "BaseProcessor",
    "PDFProcessor", "SOPProcessor", "VideoProcessor", "AudioProcessor", "WebProcessor",
    "get_processor", "ProcessorFactory",
]


def __getattr__(name: str):
    """延迟导入——仅在访问特定 processor 时才加载对应模块"""
    _import_map = {
        "PDFProcessor": ".pdf_processor",
        "SOPProcessor": ".sop_processor",
        "VideoProcessor": ".video_processor",
        "AudioProcessor": ".audio_processor",
        "WebProcessor": ".web_processor",
    }
    if name in _import_map:
        import importlib
        module = importlib.import_module(_import_map[name], __package__)
        cls = getattr(module, name)
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ProcessorFactory:
    """处理器工厂——依赖注入统一入口

    所有 Processor 通过工厂创建，自动注入共享依赖：
    - CheckpointManager（来自 Pipeline，避免每个 Processor 重复创建实例）
    """

    def __init__(self, checkpoint_manager=None):
        from ..core.checkpoint import CheckpointManager
        self._checkpoint = checkpoint_manager or CheckpointManager()

    def create(self, task_type):
        """创建处理器实例并注入依赖"""
        from ..schemas.enums import TaskType

        cls_name_map = {
            TaskType.PDF_TO_NOTE: "PDFProcessor",
            TaskType.SOP_EXTRACT: "SOPProcessor",
            TaskType.VIDEO_TO_NOTE: "VideoProcessor",
            TaskType.AUDIO_TO_NOTE: "AudioProcessor",
            TaskType.WEB_TO_NOTE: "WebProcessor",
        }
        name = cls_name_map.get(task_type)
        if name is None:
            raise ValueError(f"不支持的任务类型: {task_type}")

        processor_cls = __getattr__(name)
        processor = processor_cls()
        # 注入共享 CheckpointManager
        processor.checkpoint = self._checkpoint
        return processor


# 默认工厂实例（向后兼容）
_default_factory: ProcessorFactory | None = None


def get_processor(task_type):
    """获取处理器实例（向后兼容便捷函数）"""
    global _default_factory
    if _default_factory is None:
        _default_factory = ProcessorFactory()
    return _default_factory.create(task_type)
