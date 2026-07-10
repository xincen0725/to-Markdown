"""
SOP（Standard Operating Procedure）提取处理器

从 PDF 文档中提取标准化操作流程：
1. PDFExtractor 提取文本
2. SOPAnalyzer 识别步骤/决策点/生成文档
3. 输出标准化 SOP 文档

支持：
- 单个 PDF 提取
- 批量提取
- 重新合并已提取的 SOP
- 断点续传
"""
from __future__ import annotations

from pathlib import Path

from .base import BaseProcessor
from .pdf_extractor import PDFExtractor
from .sop_analyzer import SOPAnalyzer
from ..schemas.enums import InputSource, ErrorCategory
from ..schemas.input import ConvertRequest, SOPInput
from ..schemas.output import Result, TaskError, NoteOutput
from ..schemas.task import InternalTask
from ..core.checkpoint import CheckpointManager


class SOPProcessor(BaseProcessor):
    name = "sop_processor"

    def __init__(self):
        # checkpoint 由 ProcessorFactory 注入
        self.checkpoint: CheckpointManager = CheckpointManager()
        self._extractor = PDFExtractor()
        self._analyzer = SOPAnalyzer()

    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        inp = request.input
        if not isinstance(inp, SOPInput):
            return Result.failure(TaskError(
                code="INVALID_INPUT_TYPE",
                message=f"期望 SOPInput，实际为 {type(inp).__name__}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 幂等性检查
        if not request.force:
            cached = self.checkpoint.load(task.task_type, task.input_hash)
            if cached and cached.get("state") == "completed":
                return self._load_cached_result(cached)

        if inp.merge_existing:
            return await self._merge_existing(task, inp, request)

        if inp.source == InputSource.FILE:
            return await self._process_single(task, inp, request)
        elif inp.source == InputSource.FOLDER:
            return await self._process_folder(task, inp, request)
        else:
            return Result.failure(TaskError(
                code="UNSUPPORTED_SOURCE",
                message=f"不支持的来源: {inp.source}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

    async def _process_single(
        self, task: InternalTask, inp: SOPInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """处理单个 PDF 提取 SOP（使用 PDFExtractor + SOPAnalyzer）"""
        pdf_path = inp.path

        # 1. 提取 PDF 文本（PDFExtractor 纯逻辑模块）
        text = self._extractor.extract_text(pdf_path)

        # 2. 识别 SOP 步骤（SOPAnalyzer 纯逻辑模块）
        steps = self._analyzer.identify_steps(text)

        # 3. 识别决策点与分支
        decisions = self._analyzer.identify_decisions(text, steps)

        # 4. 生成 SOP 文档
        sop_content = self._analyzer.generate_document(
            pdf_path.stem, steps, decisions, text
        )

        # 5. 保存
        output_path = request.output.output_dir / f"{pdf_path.stem}_SOP.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(sop_content, encoding="utf-8")

        note = NoteOutput(
            title=f"{pdf_path.stem} - SOP",
            content=sop_content,
            output_path=output_path,
            source_info={
                "source": str(pdf_path),
                "type": "sop",
                "step_count": len(steps),
                "decision_count": len(decisions),
            },
        )

        self.checkpoint.mark_completed(
            task.task_type, task.input_hash, output_path,
            {"step_count": len(steps)},
        )

        return Result.success(note)

    async def _process_folder(
        self, task: InternalTask, inp: SOPInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """批量处理文件夹——每个文件独立 task 避免状态污染"""
        pdf_files = sorted(inp.path.glob("*.pdf"))
        if not pdf_files:
            return Result.failure(TaskError(
                code="NO_PDF_FILES",
                message=f"文件夹内无 PDF: {inp.path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        results = []
        errors = []
        for pdf_file in pdf_files:
            sub_inp = SOPInput(
                source=InputSource.FILE,
                path=pdf_file,
                merge_existing=False,
                output_name=pdf_file.stem,
            )
            sub_request = ConvertRequest(
                task_type=request.task_type,
                input=sub_inp,
                output=request.output,
                force=request.force,
            )
            sub_task = InternalTask.from_request(sub_request)
            result = await self._process_single(sub_task, sub_inp, sub_request)
            if result.is_success and result.data:
                results.append(result.data)
            elif result.errors:
                errors.extend(result.errors)

        if not results:
            return Result.failure(TaskError(
                code="ALL_SOP_FAILED",
                message="所有 SOP 提取失败",
                category=ErrorCategory.DEGRADED,
            ))

        # 合并所有 SOP（使用 SOPAnalyzer.merge_documents）
        merged_content, merged_stem = self._analyzer.merge_documents(
            [{"title": r.title, "content": r.content} for r in results],
            inp.output_name,
        )

        output_path = request.output.output_dir / f"{merged_stem}.md"
        output_path.write_text(merged_content, encoding="utf-8")

        merged = NoteOutput(
            title=merged_stem,
            content=merged_content,
            output_path=output_path,
            source_info={"type": "sop_merge", "file_count": len(results)},
        )

        if errors:
            return Result.partial(merged, errors, [f"{len(errors)} 个文件处理失败"])
        return Result.success(merged)

    async def _merge_existing(
        self, task: InternalTask, inp: SOPInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """重新合并已提取的 SOP 文件"""
        sop_files = sorted(request.output.output_dir.glob("*_SOP.md"))
        if not sop_files:
            return Result.failure(TaskError(
                code="NO_SOP_FILES",
                message=f"输出目录内无 SOP 文件: {request.output.output_dir}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        sop_docs = []
        for sf in sop_files:
            content = sf.read_text(encoding="utf-8")
            sop_docs.append({"title": sf.stem, "content": content})

        merged_content, merged_stem = self._analyzer.merge_documents(
            sop_docs, inp.output_name,
        )

        output_path = request.output.output_dir / f"{merged_stem}.md"
        output_path.write_text(merged_content, encoding="utf-8")

        return Result.success(NoteOutput(
            title=merged_stem,
            content=merged_content,
            output_path=output_path,
            source_info={"type": "sop_merge", "file_count": len(sop_docs)},
        ))


