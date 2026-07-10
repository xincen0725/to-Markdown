"""
视频转笔记处理器

支持：
- YouTube（自动下载字幕或转录）
- Bilibili（自动获取字幕，无字幕时转录）
- 任意网页视频
- 本地视频文件

处理流程：
1. 获取视频元信息
2. 提取/转录字幕
3. AI 总结生成结构化笔记
4. 断点续传支持
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import BaseProcessor
from ._lazy import httpx_module, yt_dlp_module
from ..schemas.enums import InputSource, ErrorCategory
from ..schemas.input import ConvertRequest, VideoInput
from ..schemas.output import Result, TaskError, NoteOutput
from ..schemas.task import InternalTask
from ..core.checkpoint import CheckpointManager
from ..core.logging import get_logger

_logger = get_logger(__name__)


class VideoProcessor(BaseProcessor):
    name = "video_processor"

    def __init__(self):
        # checkpoint 由 ProcessorFactory 注入
        self.checkpoint: CheckpointManager = CheckpointManager()

    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        inp = request.input
        if not isinstance(inp, VideoInput):
            return Result.failure(TaskError(
                code="INVALID_INPUT_TYPE",
                message=f"期望 VideoInput，实际为 {type(inp).__name__}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 幂等性检查
        if not request.force:
            cached = self.checkpoint.load(task.task_type, task.input_hash)
            if cached and cached.get("state") == "completed":
                return self._load_cached_result(cached)

        # 按来源类型分发（异常上浮到 Pipeline 统一处理）
        if inp.source == InputSource.YOUTUBE:
            transcript, metadata = await self._process_youtube(inp)
        elif inp.source == InputSource.BILIBILI:
            transcript, metadata = await self._process_bilibili(inp)
        elif inp.source == InputSource.URL:
            transcript, metadata = await self._process_web_video(inp)
        elif inp.source == InputSource.FILE:
            transcript, metadata = await self._process_local_video(inp)
        else:
            return Result.failure(TaskError(
                code="UNSUPPORTED_SOURCE",
                message=f"不支持的视频来源: {inp.source}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # AI 总结生成笔记
        note = await self._generate_note(transcript, metadata, inp, request)

        # 保存输出
        output_path = self._save_output(note, request.output)
        note.output_path = output_path

        # 保存 checkpoint
        self.checkpoint.mark_completed(
            task.task_type, task.input_hash, output_path, metadata
        )

        return Result.success(note)

    async def _process_youtube(self, inp: VideoInput) -> tuple[str, dict]:
        """处理 YouTube 视频"""
        url = inp.url_or_path
        video_id = self._extract_youtube_id(url)

        # 1. 尝试获取字幕
        transcript = ""
        if inp.use_subtitles:
            transcript = await self._get_youtube_subtitles(url, inp.language)

        # 2. 无字幕时转录
        if not transcript and inp.transcribe_if_no_subtitles:
            transcript = await self._download_and_transcribe(url, "youtube")

        metadata = {
            "source": url,
            "type": "youtube",
            "video_id": video_id,
            "language": inp.language,
        }

        return transcript, metadata

    async def _process_bilibili(self, inp: VideoInput) -> tuple[str, dict]:
        """处理 Bilibili 视频"""
        url = inp.url_or_path
        bv_id = self._extract_bilibili_id(url)

        # 1. 获取 B 站字幕
        transcript = ""
        if inp.use_subtitles:
            transcript = await self._get_bilibili_subtitles(url)

        # 2. 无字幕时转录
        if not transcript and inp.transcribe_if_no_subtitles:
            transcript = await self._download_and_transcribe(url, "bilibili")

        metadata = {
            "source": url,
            "type": "bilibili",
            "video_id": bv_id,
            "language": inp.language,
        }

        return transcript, metadata

    async def _process_web_video(self, inp: VideoInput) -> tuple[str, dict]:
        """处理任意网页视频"""
        url = inp.url_or_path
        transcript = await self._download_and_transcribe(url, "web")
        metadata = {
            "source": url,
            "type": "web_video",
            "language": inp.language,
        }
        return transcript, metadata

    async def _process_local_video(self, inp: VideoInput) -> tuple[str, dict]:
        """处理本地视频文件"""
        path = Path(inp.url_or_path)
        transcript = await self._transcribe_local_audio(path, inp.language)
        metadata = {
            "source": str(path),
            "type": "local_video",
            "filename": path.name,
            "language": inp.language,
        }
        return transcript, metadata

    # ─── 字幕获取 ───

    async def _get_youtube_subtitles(self, url: str, lang: str) -> str:
        """获取 YouTube 字幕

        使用 yt-dlp 下载字幕。
        实际实现需要调用 yt-dlp Python API。
        """
        try:
            import subprocess
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                cmd = [
                    "yt-dlp",
                    "--write-auto-subs",
                    "--sub-lang", lang,
                    "--skip-download",
                    "--output", f"{tmpdir}/%(id)s",
                    url,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    return ""

                # 查找字幕文件
                sub_files = list(Path(tmpdir).glob("*.vtt")) + list(Path(tmpdir).glob("*.srt"))
                if not sub_files:
                    return ""

                content = sub_files[0].read_text(encoding="utf-8")
                return self._parse_subtitle(content)

        except Exception as e:
            _logger.warning("YouTube 字幕获取失败: %s", e)
            return ""

    async def _get_bilibili_subtitles(self, url: str) -> str:
        """获取 Bilibili 字幕

        B站字幕通过 API 获取：https://api.bilibili.com/x/web-interface/view?bvid=...
        实际实现需要处理 B 站 API 的签名和 cookie。
        """
        try:
            bv_id = self._extract_bilibili_id(url)
            if not bv_id:
                return ""

            # B站 API 获取视频信息（懒加载 httpx）
            async with httpx_module.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.bilibili.com/x/web-interface/view",
                    params={"bvid": bv_id},
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.bilibili.com",
                    },
                    timeout=30,
                )
                if resp.status_code != 200:
                    return ""

                data = resp.json()
                if data.get("code") != 0:
                    return ""

                cid = data.get("data", {}).get("cid")
                if not cid:
                    return ""

                # 获取字幕
                sub_resp = await client.get(
                    f"https://api.bilibili.com/x/player/v2",
                    params={"bvid": bv_id, "cid": cid},
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"},
                    timeout=30,
                )
                sub_data = sub_resp.json()
                subtitle_list = sub_data.get("data", {}).get("subtitle", {}).get("subtitles", [])

                if not subtitle_list:
                    return ""

                # 获取字幕 URL
                sub_url = subtitle_list[0].get("subtitle_url", "")
                if not sub_url:
                    return ""

                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url

                sub_content = await client.get(sub_url, timeout=30)
                return self._parse_bilibili_subtitle(sub_content.json())

        except Exception as e:
            _logger.warning("Bilibili 字幕获取失败: %s", e)
            return ""

    async def _download_and_transcribe(self, url: str, source_type: str) -> str:
        """下载视频并转录音频"""
        try:
            import subprocess
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = Path(tmpdir) / "audio.wav"

                # 使用 yt-dlp 下载音频
                cmd = [
                    "yt-dlp",
                    "-x",  # 提取音频
                    "--audio-format", "wav",
                    "--audio-quality", "0",
                    "-o", str(audio_path.with_suffix("")),
                    url,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                # 查找输出文件
                wav_files = list(Path(tmpdir).glob("*.wav"))
                if not wav_files:
                    return ""

                return await self._transcribe_local_audio(wav_files[0], "zh")

        except Exception as e:
            _logger.warning("转录失败: %s", e)
            return ""

    async def _transcribe_local_audio(self, audio_path: Path, language: str) -> str:
        """转录音频文件

        实际应使用 Whisper API 或本地 Whisper 模型。
        此处提供接口框架。
        """
        # 调用 Whisper API 的模板
        # 实际实现：
        # import openai
        # client = openai.OpenAI()
        # with open(audio_path, "rb") as f:
        #     transcript = client.audio.transcriptions.create(
        #         model="whisper-1",
        #         file=f,
        #         language=language,
        #     )
        # return transcript.text

        return f"[转录内容将来自: {audio_path.name}]"

    # ─── 笔记生成 ───

    async def _generate_note(
        self,
        transcript: str,
        metadata: dict,
        inp: VideoInput,
        request: ConvertRequest,
    ) -> NoteOutput:
        """从转录文本生成结构化笔记"""
        title = metadata.get("source", "未命名视频")

        # 结构化提示词模板
        prompt = f"""
        请将以下视频转录内容转化为结构化笔记：

        要求：
        1. 标题层级清晰（## 主要章节，### 子话题）
        2. 提取关键观点、数据、结论
        3. 使用列表和引用增强可读性
        4. 末尾添加「关键要点」总结
        5. 标注时间戳（如有）

        转录内容：
        {transcript[:5000]}
        """

        # 简化处理（实际应调用 LLM）
        note_content = self._format_transcript(transcript, title)

        return NoteOutput(
            title=title,
            content=note_content,
            output_path=Path("."),
            source_info=metadata,
            processing_time_seconds=0.0,
        )

    def _format_transcript(self, transcript: str, title: str) -> str:
        """格式化转录文本为笔记"""
        if not transcript.strip():
            return f"# {title}\n\n*(无可用转录内容)*"

        lines = []
        lines.append(f"# {title}")
        lines.append("")
        lines.append("## 视频转录")
        lines.append("")

        # 按句子分割，每句一行
        sentences = re.split(r'[。！？\n]', transcript)
        for i, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) < 20:
                lines.append(f"- {sent}")
            else:
                lines.append(sent)
                lines.append("")

        lines.append("")
        lines.append("## 关键要点")
        lines.append("")
        lines.append("*(待 AI 总结)*")

        return "\n".join(lines)

    # ─── 工具方法 ───

    @staticmethod
    def _extract_youtube_id(url: str) -> str:
        """从 YouTube URL 提取视频 ID"""
        patterns = [
            r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'(?:embed/)([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_bilibili_id(url: str) -> str:
        """从 Bilibili URL 提取 BV 号"""
        # BV 号格式
        match = re.search(r'(BV[a-zA-Z0-9]{10})', url)
        if match:
            return match.group(1)
        # av 号格式
        match = re.search(r'av(\d+)', url)
        if match:
            return f"av{match.group(1)}"
        return ""

    @staticmethod
    def _parse_subtitle(content: str) -> str:
        """解析 VTT/SRT 字幕为纯文本"""
        # 去除时间戳和序号
        lines = content.split("\n")
        text_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.match(r'^\d+$', line):  # 序号
                continue
            if '-->' in line:  # 时间戳
                continue
            if line.startswith("WEBVTT"):
                continue
            # 去除 HTML 标签
            line = re.sub(r'<[^>]+>', '', line)
            text_lines.append(line)
        return " ".join(text_lines)

    @staticmethod
    def _parse_bilibili_subtitle(data: dict) -> str:
        """解析 B 站字幕 JSON"""
        body = data.get("body", [])
        texts = []
        for item in body:
            content = item.get("content", "")
            if content:
                texts.append(content)
        return " ".join(texts)


