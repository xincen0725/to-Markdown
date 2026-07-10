"""
音频转笔记处理器

支持：
- 单个音频文件：mp3, wav, m4a, ogg, flac, wma
- 批量音频文件夹
- 断点续传
"""
from __future__ import annotations

from pathlib import Path

from .base import BaseProcessor
from ..schemas.enums import InputSource, ErrorCategory
from ..schemas.input import ConvertRequest, AudioInput
from ..schemas.output import Result, TaskError, NoteOutput
from ..schemas.task import InternalTask
from ..core.checkpoint import CheckpointManager


class AudioProcessor(BaseProcessor):
    name = "audio_processor"

    def __init__(self):
        # checkpoint 由 ProcessorFactory 注入
        self.checkpoint: CheckpointManager = CheckpointManager()

    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        inp = request.input
        if not isinstance(inp, AudioInput):
            return Result.failure(TaskError(
                code="INVALID_INPUT_TYPE",
                message=f"期望 AudioInput，实际为 {type(inp).__name__}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 幂等性检查
        if not request.force:
            cached = self.checkpoint.load(task.task_type, task.input_hash)
            if cached and cached.get("state") == "completed":
                return self._load_cached_result(cached)

        # 按来源分发（异常上浮到 Pipeline 统一处理）
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
        self, task: InternalTask, inp: AudioInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """处理单个音频文件"""
        audio_path = inp.path

        # 转录
        transcript = await self._transcribe(audio_path, inp.language)

        # 生成笔记
        note = await self._generate_note(transcript, audio_path, request)

        # 保存
        output_path = self._save_output(note, request.output)
        note.output_path = output_path

        self.checkpoint.mark_completed(
            task.task_type, task.input_hash, output_path,
            {"source": str(audio_path), "language": inp.language},
        )

        return Result.success(note)

    async def _process_folder(
        self, task: InternalTask, inp: AudioInput, request: ConvertRequest
    ) -> Result[NoteOutput]:
        """批量处理音频文件夹"""
        supported = AudioInput.SUPPORTED_FORMATS
        audio_files = sorted([
            f for f in inp.path.glob("*")
            if f.suffix.lower().lstrip(".") in supported
        ])

        if not audio_files:
            return Result.failure(TaskError(
                code="NO_AUDIO_FILES",
                message=f"文件夹内无支持的音频文件: {inp.path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        results = []
        errors = []
        for audio_file in audio_files:
            sub_inp = AudioInput(
                source=InputSource.FILE,
                path=audio_file,
                language=inp.language,
            )
            sub_result = await self._process_single(task, sub_inp, request)
            if sub_result.is_success and sub_result.data:
                results.append(sub_result.data)
            elif sub_result.errors:
                errors.extend(sub_result.errors)

        if not results:
            return Result.failure(TaskError(
                code="ALL_AUDIO_FAILED",
                message="所有音频文件处理失败",
                category=ErrorCategory.DEGRADED,
            ))

        combined = NoteOutput(
            title=f"{inp.path.name}_音频笔记合集",
            content="\n\n---\n\n".join(r.content for r in results),
            output_path=request.output.output_dir / f"{inp.path.name}_音频笔记.md",
            processing_time_seconds=sum(r.processing_time_seconds for r in results),
        )

        if errors:
            return Result.partial(combined, errors, [f"{len(errors)} 个文件处理失败"])
        return Result.success(combined)

    async def _transcribe(self, audio_path: Path, language: str) -> str:
        """转录音频

        实际实现应调用 Whisper API 或本地模型。
        """
        # 模板：
        # import openai
        # client = openai.OpenAI()
        # with open(audio_path, "rb") as f:
        #     transcript = client.audio.transcriptions.create(
        #         model="whisper-1",
        #         file=f,
        #         language=language,
        #         response_format="text",
        #     )
        # return transcript

        return f"[转录内容将来自: {audio_path.name}]"

    async def _generate_note(
        self, transcript: str, audio_path: Path, request: ConvertRequest
    ) -> NoteOutput:
        """从转录生成笔记"""
        title = audio_path.stem

        # 提示词模板
        prompt = f"""
        请将以下音频转录转化为结构化笔记：

        要求：
        1. 标题清晰
        2. 提取关键信息
        3. 总结要点
        4. 保留重要引用

        转录：
        {transcript[:5000]}
        """

        content = self._format_transcript(transcript, title)

        return NoteOutput(
            title=title,
            content=content,
            output_path=Path("."),
            source_info={
                "source": str(audio_path),
                "type": "audio",
                "format": audio_path.suffix,
            },
        )

    def _format_transcript(self, transcript: str, title: str) -> str:
        """格式化转录为笔记"""
        lines = [
            f"# {title}",
            "",
            "## 音频转录",
            "",
            transcript if transcript.strip() else "*(转录中...)*",
            "",
            "## 要点总结",
            "",
            "*(待 AI 生成)*",
        ]
        return "\n".join(lines)
