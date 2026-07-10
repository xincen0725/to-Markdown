"""
PDF 转笔记处理器

处理流程：
1. PDFExtractor 分页 + 提取文本
2. OCREngine 可选 OCR
3. 分段 AI 生成笔记
4. 全局审查合并
5. 输出结构化 Markdown

支持：
- 单个 PDF / 文件夹批量
- 繁体竖排 OCR（自动判断文字方向）
- 断点续传（chunk 级别）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import BaseProcessor
from .pdf_extractor import PDFExtractor, OCREngine
from ..schemas.enums import TaskState, InputSource, ErrorCategory
from ..schemas.input import ConvertRequest, PDFInput
from ..schemas.output import Result, TaskError, NoteOutput, ChunkResult
from ..schemas.task import InternalTask, InternalChunk
from ..core.state_machine import StateMachine
from ..core.checkpoint import CheckpointManager


class PDFProcessor(BaseProcessor):
    name = "pdf_processor"

    def __init__(self):
        # checkpoint 由 ProcessorFactory 注入
        self.checkpoint: CheckpointManager = CheckpointManager()
        self._extractor = PDFExtractor()
        self._ocr_engine = OCREngine()

    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        inp = request.input
        if not isinstance(inp, PDFInput):
            return Result.failure(TaskError(
                code="INVALID_INPUT_TYPE",
                message=f"期望 PDFInput，实际为 {type(inp).__name__}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 单文件处理
        if inp.source == InputSource.FILE:
            return await self._process_single(task, inp, request)

        # 文件夹批量处理
        elif inp.source == InputSource.FOLDER:
            return await self._process_folder(task, inp, request)

        return Result.failure(TaskError(
            code="UNSUPPORTED_SOURCE",
            message=f"不支持的输入来源: {inp.source}",
            category=ErrorCategory.NON_RETRYABLE,
        ))

    async def _process_single(
        self, task: InternalTask, inp: PDFInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """处理单个 PDF 文件——异常上浮到 Pipeline 统一处理"""
        pdf_path = inp.path
        output_config = request.output

        # 检查断点续传
        if not request.force:
            cached = self.checkpoint.load(task.task_type, task.input_hash)
            if cached and cached.get("state") == TaskState.COMPLETED.value:
                return await self._load_cached_result(cached)

        # 1. 分块（使用 PDFExtractor 纯逻辑模块）
        StateMachine.transition(task, TaskState.CHUNKING, "PDF 分块处理")
        chunks = self._extractor.split_pdf(pdf_path, inp.chunk, inp.page_range)
        task.chunks = chunks

        # 2. 处理每个 chunk（断点续传）
        completed_indices = self.checkpoint.get_completed_chunks(
            task.task_type, task.input_hash
        )
        chunk_results: list[ChunkResult] = []

        for chunk in chunks:
            if chunk.index in completed_indices:
                restored = await self._restore_chunk_result(task, chunk.index)
                if restored:
                    chunk_results.append(restored)
                    continue

            result = await self._process_chunk(chunk, inp)
            chunk_results.append(result)
            task.completed_chunk_indices.add(chunk.index)
            self.checkpoint.update_chunks(
                task.task_type, task.input_hash, task.completed_chunk_indices
            )

        # 3. 合并
        StateMachine.transition(task, TaskState.MERGING, "合并分块结果")
        merged = await self._merge_and_review(chunk_results, pdf_path, inp)

        # 4. 输出
        output_path = self._get_output_path(pdf_path, output_config)
        final_content = merged.to_markdown()
        if output_config.format.value == "obsidian":
            final_content = merged.to_obsidian()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_content, encoding="utf-8")
        merged.output_path = output_path

        return Result.success(merged)

    async def _process_folder(
        self, task: InternalTask, inp: PDFInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """处理文件夹内所有 PDF——每个文件独立 task 避免状态污染"""
        pdf_files = sorted(inp.path.glob("*.pdf"))
        if not pdf_files:
            return Result.failure(TaskError(
                code="NO_PDF_FILES",
                message=f"文件夹内没有 PDF: {inp.path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        results = []
        errors = []
        for pdf_file in pdf_files:
            sub_inp = PDFInput(
                source=InputSource.FILE,
                path=pdf_file,
                page_range=inp.page_range,
                ocr=inp.ocr,
                chunk=inp.chunk,
            )
            sub_request = ConvertRequest(
                task_type=request.task_type,
                input=sub_inp,
                output=request.output,
                retry=request.retry,
                force=request.force,
                dry_run=request.dry_run,
            )
            # 每个文件使用独立的 InternalTask，避免 chunk/completed_indices 跨文件污染
            sub_task = InternalTask.from_request(sub_request)
            result = await self._process_single(sub_task, sub_inp, sub_request)
            if result.is_success and result.data:
                results.append(result.data)
            elif result.errors:
                errors.extend(result.errors)

        if not results and errors:
            return Result.failure(TaskError(
                code="BATCH_PDF_ALL_FAILED",
                message=f"所有 PDF 处理失败，共 {len(errors)} 个错误",
                category=ErrorCategory.DEGRADED,
                details={"error_count": len(errors)},
            ))

        # 合并所有结果
        combined = NoteOutput(
            title=f"{inp.path.name}_批量笔记",
            content="\n\n---\n\n".join(r.content for r in results),
            output_path=request.output.output_dir / f"{inp.path.name}_batch.md",
            total_pages=sum(r.total_pages for r in results),
            processing_time_seconds=sum(r.processing_time_seconds for r in results),
        )

        if errors:
            return Result.partial(
                combined,
                errors,
                warnings=[f"{len(errors)} 个文件处理失败"],
            )
        return Result.success(combined)

    # ─── 内部方法 ───

    async def _process_chunk(
        self, chunk: InternalChunk, inp: PDFInput
    ) -> ChunkResult:
        """处理单个 chunk（文本提取 + 可选 OCR + AI 总结）

        这里使用结构化提示词生成笔记，实际实现可接入 LLM API。
        """
        text = chunk.combined_text

        # 如果需要 OCR 且原始文本不足（使用 OCREngine 纯逻辑模块）
        if inp.ocr.enabled and len(text.strip()) < 100:
            try:
                text = self._ocr_engine.ocr_chunk(chunk, inp.ocr)
            except Exception:
                text = chunk.raw_text  # OCR 失败安全降级

        # 笔记生成（模板——实际应接入 LLM）
        note_content = self._generate_note_from_text(text, chunk.page_range)

        return ChunkResult(
            index=chunk.index,
            page_range=chunk.page_range,
            content=note_content,
            metadata=chunk.metadata,
        )

    async def _merge_and_review(
        self,
        chunk_results: list[ChunkResult],
        pdf_path: Path,
        inp: PDFInput,
    ) -> NoteOutput:
        """全局审查并合并所有 chunk"""
        # 合并内容
        merged_content_parts = []
        for cr in sorted(chunk_results, key=lambda x: x.index):
            header = f"## 第 {cr.page_range[0]}-{cr.page_range[1]} 页\n"
            merged_content_parts.append(header + cr.content)

        merged = "\n\n".join(merged_content_parts)

        # 全局审查（模板——实际应接入 LLM 进行一致性检查）
        review_prompt = f"""
        请审查以下分段生成的笔记，确保：
        1. 逻辑连贯性
        2. 无重复内容
        3. 术语一致性
        4. 结构完整性

        笔记内容：
        {merged[:2000]}...
        """
        # 这里实际应调用 LLM API
        reviewed_content = merged  # 暂时保持原样

        return NoteOutput(
            title=pdf_path.stem,
            content=reviewed_content,
            output_path=Path("."),  # 外部设置
            source_info={
                "source": str(pdf_path),
                "type": "pdf",
                "tags": ["pdf-note"],
            },
            chunk_count=len(chunk_results),
            total_pages=sum(
                cr.page_range[1] - cr.page_range[0] + 1
                for cr in chunk_results
            ),
        )

    def _generate_note_from_text(
        self, text: str, page_range: tuple[int, int]
    ) -> str:
        """从文本生成结构化笔记

        这是模板方法——实际应接入 LLM API。
        此处提供结构化提示词框架。
        """
        if not text.strip():
            return f"*(第 {page_range[0]}-{page_range[1]} 页：无可提取文本)*"

        # 结构化提示词模板
        prompt = f"""
        请将以下文本转化为结构化笔记，遵循规则：
        1. 提取核心观点和关键信息
        2. 使用 Markdown 格式（标题、列表、引用）
        3. 保留重要数据、日期、人名
        4. 对于古籍/竖排文献：保留原文格式，添加现代文注释
        5. 每段标注来源页码

        文本：
        {text[:3000]}
        """
        # 简化处理：直接返回格式化文本（实际应调用 LLM）
        lines = text.strip().split("\n")
        formatted = []
        for line in lines[:50]:  # 限制行数
            line = line.strip()
            if not line:
                formatted.append("")
            elif len(line) < 80 and line.endswith(("。", "）", "》", "：", ":")):
                formatted.append(f"**{line}**")
            else:
                formatted.append(line)

        return "\n\n".join(formatted)

    def _get_output_path(self, pdf_path: Path, output_config) -> Path:
        """生成输出路径"""
        ext = ".md"
        return output_config.output_dir / f"{pdf_path.stem}_笔记{ext}"

    async def _restore_chunk_result(
        self, task: InternalTask, chunk_index: int
    ) -> Optional[ChunkResult]:
        """从 checkpoint 恢复 chunk 结果"""
        # 从 task.chunks 查找匹配的 chunk 获取真实 page_range
        matching = [c for c in task.chunks if c.index == chunk_index]
        page_range = matching[0].page_range if matching else (0, 0)

        output_path = task.output_dir / f"chunk_{chunk_index}.md"
        if output_path.exists():
            return ChunkResult(
                index=chunk_index,
                page_range=page_range,
                content=output_path.read_text(encoding="utf-8"),
            )
        return None
