"""
to-Markdown 主入口

提供：
1. CLI 命令行接口（自动安装依赖）
2. Python API
3. MCP Server 启动入口
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from .bootstrap import ensure_cli_deps
from .schemas.enums import TaskType, InputSource, OutputFormat, OCRDirection
from .schemas.input import (
    ConvertRequest, PDFInput, SOPInput, VideoInput, AudioInput, WebInput,
    OCRConfig, ChunkConfig, OutputConfig, RetryConfig,
)
from .schemas.output import Result, NoteOutput, BatchResult
from .core.pipeline import Pipeline
from .core.anticorruption import TaskValidator


# ─── Python API ───

class ToMarkdown:
    """to-Markdown 主 API 类"""

    def __init__(
        self,
        output_dir: str | Path = "./output",
        checkpoint_dir: str | Path | None = None,
        max_concurrency: int = 4,
    ):
        self.output_dir = Path(output_dir)
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.pipeline = Pipeline(
            checkpoint_dir=self.checkpoint_dir,
            max_concurrency=max_concurrency,
        )

    @staticmethod
    def _unwrap_validation(result: Result) -> Result[NoteOutput]:
        """将防腐层校验结果转换为 API 返回类型

        TaskValidator.validate() 返回 Result[ConvertRequest]，
        但 API 方法声明返回 Result[NoteOutput]。
        校验失败时提取错误信息重新封装。
        """
        if result.is_failure:
            return Result(
                state=result.state,
                errors=result.errors,
                warnings=result.warnings,
            )
        # 不应到达此处（校验通过时 is_failure=False）
        return Result.failure(TaskError(
            code="INTERNAL",
            message="校验结果异常",
            category=ErrorCategory.NON_RETRYABLE,
        ))

    async def pdf_to_note(
        self,
        path: str | Path,
        source: str = "file",
        pages_per_chunk: int = 10,
        ocr_enabled: bool = True,
        ocr_language: str = "chi_tra+chi_sim+eng",
        ocr_direction: str = "auto",
        page_range: Optional[tuple[int, int]] = None,
        force: bool = False,
    ) -> Result[NoteOutput]:
        """PDF 转笔记"""
        request = ConvertRequest(
            task_type=TaskType.PDF_TO_NOTE,
            input=PDFInput(
                source=InputSource(source),
                path=Path(path),
                page_range=page_range,
                ocr=OCRConfig(
                    enabled=ocr_enabled,
                    direction=OCRDirection(ocr_direction),
                    language=ocr_language,
                ),
                chunk=ChunkConfig(pages_per_chunk=pages_per_chunk),
            ),
            output=OutputConfig(output_dir=self.output_dir),
            force=force,
        )

        # 防腐层校验
        validated = TaskValidator.validate(request)
        if validated.is_failure:
            return self._unwrap_validation(validated)

        return await self.pipeline.execute(request)

    async def video_to_note(
        self,
        url: str,
        source: str = "youtube",
        language: str = "zh",
        use_subtitles: bool = True,
        force: bool = False,
    ) -> Result[NoteOutput]:
        """视频转笔记"""
        request = ConvertRequest(
            task_type=TaskType.VIDEO_TO_NOTE,
            input=VideoInput(
                source=InputSource(source),
                url_or_path=url,
                language=language,
                use_subtitles=use_subtitles,
            ),
            output=OutputConfig(output_dir=self.output_dir),
            force=force,
        )

        validated = TaskValidator.validate(request)
        if validated.is_failure:
            return self._unwrap_validation(validated)

        return await self.pipeline.execute(request)

    async def audio_to_note(
        self,
        path: str | Path,
        source: str = "file",
        language: str = "zh",
        force: bool = False,
    ) -> Result[NoteOutput]:
        """音频转笔记"""
        request = ConvertRequest(
            task_type=TaskType.AUDIO_TO_NOTE,
            input=AudioInput(
                source=InputSource(source),
                path=Path(path),
                language=language,
            ),
            output=OutputConfig(output_dir=self.output_dir),
            force=force,
        )

        validated = TaskValidator.validate(request)
        if validated.is_failure:
            return self._unwrap_validation(validated)

        return await self.pipeline.execute(request)

    async def web_to_note(
        self,
        url: str,
        extract_main: bool = True,
        force: bool = False,
    ) -> Result[NoteOutput]:
        """网页转笔记"""
        request = ConvertRequest(
            task_type=TaskType.WEB_TO_NOTE,
            input=WebInput(url=url, extract_main_content=extract_main),
            output=OutputConfig(output_dir=self.output_dir),
            force=force,
        )

        validated = TaskValidator.validate(request)
        if validated.is_failure:
            return self._unwrap_validation(validated)

        return await self.pipeline.execute(request)

    async def extract_sop(
        self,
        path: str | Path,
        source: str = "file",
        merge_existing: bool = False,
        force: bool = False,
    ) -> Result[NoteOutput]:
        """SOP 提取"""
        request = ConvertRequest(
            task_type=TaskType.SOP_EXTRACT,
            input=SOPInput(
                source=InputSource(source),
                path=Path(path),
                merge_existing=merge_existing,
            ),
            output=OutputConfig(output_dir=self.output_dir),
            force=force,
        )

        validated = TaskValidator.validate(request)
        if validated.is_failure:
            return self._unwrap_validation(validated)

        return await self.pipeline.execute(request)

    async def batch_convert(
        self,
        requests: list[ConvertRequest],
    ) -> BatchResult:
        """批量转换"""
        # 校验所有请求
        valid_requests = []
        for req in requests:
            validated = TaskValidator.validate(req)
            if validated.is_success:
                valid_requests.append(req)

        return await self.pipeline.execute_batch(valid_requests)


# ─── CLI ───

def create_cli():
    """创建 CLI 应用（自动安装 CLI 依赖）"""
    ensure_cli_deps()
    import typer

    app = typer.Typer(
        name="to-markdown",
        help="多源输入转结构化 Markdown 笔记",
    )

    @app.command()
    def pdf(
        path: str = typer.Argument(..., help="PDF 文件或文件夹路径"),
        source: str = typer.Option("file", help="输入类型: file/folder"),
        output_dir: str = typer.Option("./output", help="输出目录"),
        pages_per_chunk: int = typer.Option(10, help="每块页数"),
        ocr: bool = typer.Option(True, help="启用 OCR"),
        ocr_lang: str = typer.Option("chi_tra+chi_sim+eng", help="OCR 语言"),
        ocr_dir: str = typer.Option("auto", help="文字方向: auto/horizontal/vertical"),
        force: bool = typer.Option(False, "--force", help="强制重新处理"),
    ):
        """PDF 转结构化笔记"""
        async def run():
            tm = ToMarkdown(output_dir=output_dir)
            result = await tm.pdf_to_note(
                path=path,
                source=source,
                pages_per_chunk=pages_per_chunk,
                ocr_enabled=ocr,
                ocr_language=ocr_lang,
                ocr_direction=ocr_dir,
                force=force,
            )
            _print_result(result)

        asyncio.run(run())

    @app.command()
    def video(
        url: str = typer.Argument(..., help="视频 URL"),
        source: str = typer.Option("youtube", help="来源: youtube/bilibili/url/file"),
        output_dir: str = typer.Option("./output", help="输出目录"),
        lang: str = typer.Option("zh", help="音频语言"),
        subtitles: bool = typer.Option(True, help="使用字幕"),
        force: bool = typer.Option(False, "--force", help="强制重新处理"),
    ):
        """视频转结构化笔记"""
        async def run():
            tm = ToMarkdown(output_dir=output_dir)
            result = await tm.video_to_note(
                url=url,
                source=source,
                language=lang,
                use_subtitles=subtitles,
                force=force,
            )
            _print_result(result)

        asyncio.run(run())

    @app.command()
    def audio(
        path: str = typer.Argument(..., help="音频文件或文件夹路径"),
        source: str = typer.Option("file", help="输入类型: file/folder"),
        output_dir: str = typer.Option("./output", help="输出目录"),
        lang: str = typer.Option("zh", help="音频语言"),
        force: bool = typer.Option(False, "--force", help="强制重新处理"),
    ):
        """音频转结构化笔记"""
        async def run():
            tm = ToMarkdown(output_dir=output_dir)
            result = await tm.audio_to_note(
                path=path,
                source=source,
                language=lang,
                force=force,
            )
            _print_result(result)

        asyncio.run(run())

    @app.command()
    def web(
        url: str = typer.Argument(..., help="网页 URL"),
        output_dir: str = typer.Option("./output", help="输出目录"),
        full_page: bool = typer.Option(False, help="保留完整页面（不提取正文）"),
        force: bool = typer.Option(False, "--force", help="强制重新处理"),
    ):
        """网页转结构化笔记"""
        async def run():
            tm = ToMarkdown(output_dir=output_dir)
            result = await tm.web_to_note(
                url=url,
                extract_main=not full_page,
                force=force,
            )
            _print_result(result)

        asyncio.run(run())

    @app.command()
    def sop(
        path: str = typer.Argument(..., help="PDF 文件或文件夹路径"),
        source: str = typer.Option("file", help="输入类型: file/folder"),
        output_dir: str = typer.Option("./output", help="输出目录"),
        merge: bool = typer.Option(False, help="重新合并已提取的 SOP"),
        force: bool = typer.Option(False, "--force", help="强制重新处理"),
    ):
        """从 PDF 提取 SOP"""
        async def run():
            tm = ToMarkdown(output_dir=output_dir)
            result = await tm.extract_sop(
                path=path,
                source=source,
                merge_existing=merge,
                force=force,
            )
            _print_result(result)

        asyncio.run(run())

    return app


def _print_result(result: Result[NoteOutput]) -> None:
    """打印结果"""
    if result.is_success and result.data:
        print(f"✅ 成功: {result.data.output_path}")
        print(f"   标题: {result.data.title}")
        print(f"   页数: {result.data.total_pages}")
        print(f"   分块: {result.data.chunk_count}")
        if result.metadata.get("cached"):
            print("   (来自缓存)")
    elif result.is_partial and result.data:
        print(f"⚠️  部分成功: {result.data.output_path}")
        for err in result.errors:
            print(f"   错误: {err.message}")
    else:
        print("❌ 失败:")
        for err in result.errors:
            print(f"   [{err.category.value}] {err.message}")

    if result.warnings:
        for w in result.warnings:
            print(f"⚠️  {w}")


# ─── 入口点 ───

app = create_cli()

if __name__ == "__main__":
    app()
