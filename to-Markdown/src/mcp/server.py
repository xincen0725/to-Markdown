"""
MCP Server 入口

将 to-Markdown 功能暴露为 MCP 工具，可供 AI 助手直接调用。

工具列表：
- convert_pdf: PDF 转笔记
- convert_video: 视频转笔记
- convert_audio: 音频转笔记
- convert_web: 网页转笔记
- extract_sop: SOP 提取
- batch_convert: 批量转换
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..schemas.enums import TaskType, InputSource, OutputFormat, OCRDirection
from ..schemas.input import (
    ConvertRequest, PDFInput, SOPInput, VideoInput, AudioInput, WebInput,
    OCRConfig, ChunkConfig, OutputConfig, RetryConfig,
)
from ..schemas.output import Result, NoteOutput, BatchResult
from ..core.pipeline import Pipeline


class MCPServer:
    """MCP Server 实现

    实际部署时，使用 mcp 库的标准 Server 接口。
    此处提供核心逻辑，便于集成。
    """

    def __init__(self, checkpoint_dir: Optional[Path] = None):
        self.pipeline = Pipeline(checkpoint_dir=checkpoint_dir)

    # ─── MCP 工具定义 ───

    async def convert_pdf(
        self,
        path: str,
        source: str = "file",
        output_dir: str = "./output",
        pages_per_chunk: int = 10,
        ocr_enabled: bool = True,
        ocr_language: str = "chi_tra+chi_sim+eng",
        ocr_direction: str = "auto",
        force: bool = False,
    ) -> dict:
        """PDF 转笔记

        Args:
            path: PDF 文件或文件夹路径
            source: "file" 或 "folder"
            output_dir: 输出目录
            pages_per_chunk: 每块页数
            ocr_enabled: 是否启用 OCR
            ocr_language: OCR 语言代码
            ocr_direction: 文字方向 (auto/horizontal/vertical)
            force: 强制重新处理
        """
        request = ConvertRequest(
            task_type=TaskType.PDF_TO_NOTE,
            input=PDFInput(
                source=InputSource(source),
                path=Path(path),
                ocr=OCRConfig(
                    enabled=ocr_enabled,
                    direction=OCRDirection(ocr_direction),
                    language=ocr_language,
                ),
                chunk=ChunkConfig(pages_per_chunk=pages_per_chunk),
            ),
            output=OutputConfig(output_dir=Path(output_dir)),
            force=force,
        )
        result = await self.pipeline.execute(request)
        return self._result_to_dict(result)

    async def convert_video(
        self,
        url: str,
        source: str = "youtube",
        language: str = "zh",
        use_subtitles: bool = True,
        output_dir: str = "./output",
        force: bool = False,
    ) -> dict:
        """视频转笔记

        Args:
            url: 视频 URL
            source: 来源 (youtube/bilibili/url/file)
            language: 音频语言
            use_subtitles: 是否使用字幕
            output_dir: 输出目录
            force: 强制重新处理
        """
        request = ConvertRequest(
            task_type=TaskType.VIDEO_TO_NOTE,
            input=VideoInput(
                source=InputSource(source),
                url_or_path=url,
                language=language,
                use_subtitles=use_subtitles,
            ),
            output=OutputConfig(output_dir=Path(output_dir)),
            force=force,
        )
        result = await self.pipeline.execute(request)
        return self._result_to_dict(result)

    async def convert_audio(
        self,
        path: str,
        source: str = "file",
        language: str = "zh",
        output_dir: str = "./output",
        force: bool = False,
    ) -> dict:
        """音频转笔记

        Args:
            path: 音频文件或文件夹路径
            source: "file" 或 "folder"
            language: 音频语言
            output_dir: 输出目录
            force: 强制重新处理
        """
        request = ConvertRequest(
            task_type=TaskType.AUDIO_TO_NOTE,
            input=AudioInput(
                source=InputSource(source),
                path=Path(path),
                language=language,
            ),
            output=OutputConfig(output_dir=Path(output_dir)),
            force=force,
        )
        result = await self.pipeline.execute(request)
        return self._result_to_dict(result)

    async def convert_web(
        self,
        url: str,
        extract_main: bool = True,
        output_dir: str = "./output",
        force: bool = False,
    ) -> dict:
        """网页转笔记

        Args:
            url: 网页 URL
            extract_main: 是否仅提取正文
            output_dir: 输出目录
            force: 强制重新处理
        """
        request = ConvertRequest(
            task_type=TaskType.WEB_TO_NOTE,
            input=WebInput(
                url=url,
                extract_main_content=extract_main,
            ),
            output=OutputConfig(output_dir=Path(output_dir)),
            force=force,
        )
        result = await self.pipeline.execute(request)
        return self._result_to_dict(result)

    async def extract_sop(
        self,
        path: str,
        source: str = "file",
        output_dir: str = "./output",
        force: bool = False,
    ) -> dict:
        """SOP 提取

        Args:
            path: PDF 文件或文件夹路径
            source: "file" 或 "folder"
            output_dir: 输出目录
            force: 强制重新处理
        """
        request = ConvertRequest(
            task_type=TaskType.SOP_EXTRACT,
            input=SOPInput(
                source=InputSource(source),
                path=Path(path),
            ),
            output=OutputConfig(output_dir=Path(output_dir)),
            force=force,
        )
        result = await self.pipeline.execute(request)
        return self._result_to_dict(result)

    async def batch_convert(
        self,
        tasks: list[dict],
        output_dir: str = "./output",
        force: bool = False,
    ) -> dict:
        """批量转换

        Args:
            tasks: 任务列表，每个任务包含 type 和对应参数
            output_dir: 输出目录
            force: 强制重新处理
        """
        requests = []
        for task in tasks:
            task_type = TaskType(task["type"])
            request = self._build_request(task_type, task, output_dir, force)
            requests.append(request)

        result = await self.pipeline.execute_batch(requests)
        return self._batch_result_to_dict(result)

    # ─── 工具方法 ───

    def _build_request(
        self, task_type: TaskType, params: dict, output_dir: str, force: bool
    ) -> ConvertRequest:
        """根据参数构建请求（含参数范围校验）"""
        output = OutputConfig(output_dir=Path(output_dir))

        if task_type == TaskType.PDF_TO_NOTE:
            if "path" not in params:
                raise ValueError("PDF 任务缺少必填参数: path")
            pages_per_chunk = params.get("pages_per_chunk", 10)
            if not (1 <= pages_per_chunk <= 100):
                raise ValueError(f"pages_per_chunk 必须在 1-100 之间，实际: {pages_per_chunk}")
            return ConvertRequest(
                task_type=task_type,
                input=PDFInput(
                    source=InputSource(params.get("source", "file")),
                    path=Path(params["path"]),
                    ocr=OCRConfig(
                        enabled=params.get("ocr_enabled", True),
                        language=params.get("ocr_language", "chi_tra+chi_sim+eng"),
                        direction=OCRDirection(params.get("ocr_direction", "auto")),
                    ),
                    chunk=ChunkConfig(pages_per_chunk=pages_per_chunk),
                ),
                output=output,
                force=force,
            )
        elif task_type == TaskType.VIDEO_TO_NOTE:
            if "url" not in params:
                raise ValueError("视频任务缺少必填参数: url")
            return ConvertRequest(
                task_type=task_type,
                input=VideoInput(
                    source=InputSource(params.get("source", "youtube")),
                    url_or_path=params["url"],
                    language=params.get("language", "zh"),
                    use_subtitles=params.get("use_subtitles", True),
                ),
                output=output,
                force=force,
            )
        elif task_type == TaskType.AUDIO_TO_NOTE:
            if "path" not in params:
                raise ValueError("音频任务缺少必填参数: path")
            return ConvertRequest(
                task_type=task_type,
                input=AudioInput(
                    source=InputSource(params.get("source", "file")),
                    path=Path(params["path"]),
                    language=params.get("language", "zh"),
                ),
                output=output,
                force=force,
            )
        elif task_type == TaskType.WEB_TO_NOTE:
            if "url" not in params:
                raise ValueError("网页任务缺少必填参数: url")
            return ConvertRequest(
                task_type=task_type,
                input=WebInput(
                    url=params["url"],
                    extract_main_content=params.get("extract_main", True),
                ),
                output=output,
                force=force,
            )
        elif task_type == TaskType.SOP_EXTRACT:
            if "path" not in params:
                raise ValueError("SOP 任务缺少必填参数: path")
            return ConvertRequest(
                task_type=task_type,
                input=SOPInput(
                    source=InputSource(params.get("source", "file")),
                    path=Path(params["path"]),
                ),
                output=output,
                force=force,
            )
        else:
            raise ValueError(f"不支持的任务类型: {task_type}")

    def _result_to_dict(self, result: Result[NoteOutput]) -> dict:
        """Result → dict（MCP 返回格式）"""
        base = {
            "success": result.is_success,
            "state": result.state.value,
            "warnings": result.warnings,
        }

        if result.data:
            base["output"] = {
                "title": result.data.title,
                "output_path": str(result.data.output_path),
                "total_pages": result.data.total_pages,
                "chunk_count": result.data.chunk_count,
                "processing_time_seconds": result.data.processing_time_seconds,
            }

        if result.errors:
            base["errors"] = [e.to_dict() for e in result.errors]

        if result.metadata:
            base["metadata"] = result.metadata

        return base

    def _batch_result_to_dict(self, result: BatchResult) -> dict:
        """BatchResult → dict"""
        return {
            "success": result.failed == 0,
            "total": result.total,
            "completed": result.completed,
            "failed": result.failed,
            "skipped": result.skipped,
            "success_rate": result.success_rate,
            "results": [self._result_to_dict(r) for r in result.results],
        }
