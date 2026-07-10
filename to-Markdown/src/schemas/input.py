"""
输入 Schema —— 防腐层第1层

所有外部输入必须经过此层的严格校验。
使用 Pydantic v2 的 field_validator 进行：
1. 类型校验
2. 范围校验
3. 格式校验
4. 互斥约束校验
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import ClassVar, Optional, Union, Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ValidationInfo,
)

from .enums import TaskType, InputSource, OutputFormat, OCRDirection


class OCRConfig(BaseModel):
    """OCR 配置"""
    enabled: bool = Field(default=True, description="是否启用 OCR")
    direction: OCRDirection = Field(
        default=OCRDirection.AUTO,
        description="文字方向：horizontal/vertical/auto"
    )
    language: str = Field(
        default="chi_tra+chi_sim+eng",
        description="OCR 语言代码，如 chi_tra+eng"
    )
    dpi: int = Field(default=300, ge=72, le=1200, description="OCR DPI")


class ChunkConfig(BaseModel):
    """分块配置"""
    pages_per_chunk: int = Field(
        default=10, ge=1, le=100,
        description="每块的页数"
    )
    overlap_pages: int = Field(
        default=1, ge=0, le=10,
        description="块间重叠页数"
    )

    @model_validator(mode="after")
    def check_overlap(self) -> "ChunkConfig":
        if self.overlap_pages >= self.pages_per_chunk:
            raise ValueError(
                f"overlap_pages ({self.overlap_pages}) must be < "
                f"pages_per_chunk ({self.pages_per_chunk})"
            )
        return self


class OutputConfig(BaseModel):
    """输出配置"""
    output_dir: Path = Field(
        default=Path("./output"),
        description="输出目录"
    )
    format: OutputFormat = Field(
        default=OutputFormat.MARKDOWN,
        description="输出格式"
    )
    obsidian_vault: Optional[Path] = Field(
        default=None,
        description="Obsidian 仓库路径（format=obsidian 时必填）"
    )
    obsidian_subdir: str = Field(
        default="notes",
        description="Obsidian 仓库内子目录"
    )
    overwrite: bool = Field(
        default=False,
        description="是否覆盖已有输出"
    )

    @model_validator(mode="after")
    def check_obsidian_config(self) -> "OutputConfig":
        if self.format == OutputFormat.OBSIDIAN and self.obsidian_vault is None:
            raise ValueError("输出格式为 obsidian 时，必须指定 obsidian_vault")
        return self


class RetryConfig(BaseModel):
    """重试配置"""
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    base_delay_seconds: float = Field(default=1.0, ge=0.1, description="基础退避延迟（秒）")
    max_delay_seconds: float = Field(default=60.0, ge=1.0, description="最大退避延迟（秒）")
    timeout_seconds: float = Field(default=300.0, ge=10.0, description="全局超时（秒）")


class PDFInput(BaseModel):
    """PDF 输入"""
    source: Literal[InputSource.FILE, InputSource.FOLDER]
    path: Path = Field(description="PDF 文件或文件夹路径")
    page_range: Optional[tuple[int, int]] = Field(
        default=None,
        description="页码范围 (start, end)，1-indexed，None=全部"
    )
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: Path) -> Path:
        v = v.resolve()
        if not v.exists():
            raise ValueError(f"路径不存在: {v}")
        return v

    @model_validator(mode="after")
    def check_source_type(self) -> "PDFInput":
        if self.source == InputSource.FILE and not self.path.is_file():
            raise ValueError(f"source=file 但路径不是文件: {self.path}")
        if self.source == InputSource.FOLDER and not self.path.is_dir():
            raise ValueError(f"source=folder 但路径不是目录: {self.path}")
        return self

    @model_validator(mode="after")
    def check_page_range(self) -> "PDFInput":
        if self.page_range is not None:
            start, end = self.page_range
            if start < 1:
                raise ValueError(f"page_range start 必须 >= 1，实际: {start}")
            if end < start:
                raise ValueError(f"page_range end ({end}) 必须 >= start ({start})")
        return self


class SOPInput(BaseModel):
    """SOP 提取输入"""
    source: Literal[InputSource.FILE, InputSource.FOLDER]
    path: Path = Field(description="PDF 文件或文件夹路径")
    merge_existing: bool = Field(
        default=False,
        description="是否重新合并已提取的 SOP"
    )
    output_name: str = Field(
        default="extracted_sop",
        description="输出文件名（不含扩展名）"
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: Path) -> Path:
        v = v.resolve()
        if not v.exists():
            raise ValueError(f"路径不存在: {v}")
        return v


class VideoInput(BaseModel):
    """视频输入"""
    source: Literal[InputSource.YOUTUBE, InputSource.BILIBILI, InputSource.URL, InputSource.FILE]
    url_or_path: str = Field(description="视频 URL 或本地路径")
    language: str = Field(default="zh", description="音频语言代码")
    use_subtitles: bool = Field(default=True, description="优先使用字幕")
    transcribe_if_no_subtitles: bool = Field(
        default=True,
        description="无字幕时是否转录音频"
    )

    @field_validator("url_or_path")
    @classmethod
    def validate_url_or_path(cls, v: str, info: ValidationInfo) -> str:
        source = info.data.get("source")
        if source == InputSource.FILE:
            p = Path(v).resolve()
            if not p.exists():
                raise ValueError(f"文件不存在: {v}")
        elif source in (InputSource.YOUTUBE, InputSource.BILIBILI, InputSource.URL):
            if not v.startswith(("http://", "https://")):
                raise ValueError(f"URL 格式无效: {v}")
        return v


class AudioInput(BaseModel):
    """音频输入"""
    source: Literal[InputSource.FILE, InputSource.FOLDER]
    path: Path = Field(description="音频文件或文件夹路径")
    language: str = Field(default="zh", description="音频语言代码")

    SUPPORTED_FORMATS: ClassVar[frozenset[str]] = frozenset({"mp3", "wav", "m4a", "ogg", "flac", "wma"})

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: Path, info: ValidationInfo) -> Path:
        v = v.resolve()
        if not v.exists():
            raise ValueError(f"路径不存在: {v}")
        source = info.data.get("source")
        if source == InputSource.FILE:
            if v.suffix.lower().lstrip(".") not in cls.SUPPORTED_FORMATS:
                raise ValueError(
                    f"不支持的音频格式: {v.suffix}，支持: {cls.SUPPORTED_FORMATS}"
                )
        return v


class WebInput(BaseModel):
    """网页输入"""
    url: str = Field(description="网页 URL")
    extract_main_content: bool = Field(
        default=True,
        description="是否仅提取正文（去除导航/广告等）"
    )
    include_images: bool = Field(
        default=False,
        description="是否包含图片描述"
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL 格式无效: {v}")
        return v


# ─── 联合输入类型 ───

InputUnion = Union[PDFInput, SOPInput, VideoInput, AudioInput, WebInput]


class ConvertRequest(BaseModel):
    """统一的转换请求——外部入口"""
    task_type: TaskType = Field(description="任务类型")
    input: InputUnion = Field(description="输入参数")
    output: OutputConfig = Field(default_factory=OutputConfig, description="输出配置")
    retry: RetryConfig = Field(default_factory=RetryConfig, description="重试配置")
    force: bool = Field(default=False, description="强制重新处理（忽略 checkpoint）")
    dry_run: bool = Field(default=False, description="仅校验，不实际执行")

    @model_validator(mode="after")
    def check_type_input_match(self) -> "ConvertRequest":
        """校验 task_type 与 input 类型匹配"""
        type_map = {
            TaskType.PDF_TO_NOTE: PDFInput,
            TaskType.SOP_EXTRACT: SOPInput,
            TaskType.VIDEO_TO_NOTE: VideoInput,
            TaskType.AUDIO_TO_NOTE: AudioInput,
            TaskType.WEB_TO_NOTE: WebInput,
        }
        expected = type_map.get(self.task_type)
        if expected is not None and not isinstance(self.input, expected):
            raise ValueError(
                f"task_type={self.task_type.value} 需要 {expected.__name__}，"
                f"实际为 {type(self.input).__name__}"
            )
        return self

    def compute_input_hash(self) -> str:
        """计算输入参数的 SHA256 哈希——幂等性关键"""
        # 排除 output.overwrite 和 retry、force、dry_run（不影响输出内容）
        content_dict = {
            "task_type": self.task_type.value,
            "input": self.input.model_dump(exclude={"path"} if hasattr(self.input, "path") else set()),
        }
        # 如果有 path，加入文件内容哈希而非路径
        if hasattr(self.input, "path"):
            p = self.input.path
            if p.is_file():
                content_dict["file_hash"] = self._file_sha256(p)
            elif p.is_dir():
                # 文件夹：使用文件列表的哈希
                files = sorted([f.name for f in p.glob("*") if f.is_file()])
                content_dict["file_list_hash"] = hashlib.sha256(
                    json.dumps(files, sort_keys=True).encode()
                ).hexdigest()
        return hashlib.sha256(
            json.dumps(content_dict, sort_keys=True, default=str).encode()
        ).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        """计算文件的 SHA256"""
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
