"""
防腐层 —— 第2层校验

在 Pydantic 校验（第1层）之后，进行业务规则校验：
1. 文件存在性与可读性
2. 权限检查
3. 输出目录可写性
4. 外部依赖可用性检查

所有校验 fail-fast，不进入处理流程。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from ..schemas.enums import TaskType, InputSource, ErrorCategory
from ..schemas.input import (
    ConvertRequest,
    PDFInput,
    SOPInput,
    VideoInput,
    AudioInput,
    WebInput,
)
from ..schemas.output import TaskError, Result


class TaskValidator:
    """任务校验器——防腐层第2层

    校验失败直接返回 Result.failure，不抛异常。
    """

    @classmethod
    def validate(cls, request: ConvertRequest) -> Result[ConvertRequest]:
        """完整校验"""
        errors: list[TaskError] = []
        warnings: list[str] = []

        # 1. 输出目录校验
        errors.extend(cls._validate_output_dir(request))
        # 2. 输入特有校验
        errors.extend(cls._validate_input(request))
        # 3. 外部依赖检查
        dep_errors, dep_warnings = cls._check_dependencies(request)
        errors.extend(dep_errors)
        warnings.extend(dep_warnings)

        if errors:
            return Result.failure(
                TaskError(
                    code="VALIDATION_FAILED",
                    message=f"校验失败，共 {len(errors)} 个错误",
                    category=ErrorCategory.NON_RETRYABLE,
                    details={"errors": [e.to_dict() for e in errors]},
                )
            )

        if warnings:
            result = Result.success(request, warnings=warnings)
            result.warnings = warnings
            return result

        return Result.success(request)

    @classmethod
    def _validate_output_dir(cls, request: ConvertRequest) -> list[TaskError]:
        errors = []
        output_dir = request.output.output_dir.resolve()

        # 检查输出目录父目录是否存在
        parent = output_dir
        while not parent.exists():
            parent = parent.parent
        if not os.access(parent, os.W_OK):
            errors.append(TaskError(
                code="OUTPUT_DIR_NOT_WRITABLE",
                message=f"输出目录不可写: {output_dir}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 创建输出目录（如果不存在）
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            errors.append(TaskError(
                code="OUTPUT_DIR_CREATE_FAILED",
                message=f"无法创建输出目录: {e}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        return errors

    @classmethod
    def _validate_input(cls, request: ConvertRequest) -> list[TaskError]:
        errors = []
        inp = request.input

        if isinstance(inp, (PDFInput, SOPInput, AudioInput)):
            if inp.source == InputSource.FILE:
                errors.extend(cls._check_file_readable(inp.path))
            elif inp.source == InputSource.FOLDER:
                errors.extend(cls._check_folder_readable(inp.path))

        if isinstance(inp, AudioInput) and inp.source == InputSource.FOLDER:
            # 检查文件夹内是否有支持的音频文件
            supported = AudioInput.SUPPORTED_FORMATS
            audio_files = [
                f for f in inp.path.glob("*")
                if f.suffix.lower().lstrip(".") in supported
            ]
            if not audio_files:
                errors.append(TaskError(
                    code="NO_AUDIO_FILES",
                    message=f"文件夹内没有支持的音频文件 ({', '.join(sorted(supported))}): {inp.path}",
                    category=ErrorCategory.NON_RETRYABLE,
                ))

        if isinstance(inp, PDFInput) and inp.source == InputSource.FOLDER:
            pdf_files = list(inp.path.glob("*.pdf"))
            if not pdf_files:
                errors.append(TaskError(
                    code="NO_PDF_FILES",
                    message=f"文件夹内没有 PDF 文件: {inp.path}",
                    category=ErrorCategory.NON_RETRYABLE,
                ))

        return errors

    @classmethod
    def _check_file_readable(cls, path: Path) -> list[TaskError]:
        errors = []
        if not path.is_file():
            errors.append(TaskError(
                code="NOT_A_FILE",
                message=f"路径不是文件: {path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))
        elif not os.access(path, os.R_OK):
            errors.append(TaskError(
                code="FILE_NOT_READABLE",
                message=f"文件不可读: {path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))
        return errors

    @classmethod
    def _check_folder_readable(cls, path: Path) -> list[TaskError]:
        errors = []
        if not path.is_dir():
            errors.append(TaskError(
                code="NOT_A_DIRECTORY",
                message=f"路径不是目录: {path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))
        elif not os.access(path, os.R_OK):
            errors.append(TaskError(
                code="DIR_NOT_READABLE",
                message=f"目录不可读: {path}",
                category=ErrorCategory.NON_RETRYABLE,
            ))
        return errors

    @classmethod
    def _check_dependencies(cls, request: ConvertRequest) -> tuple[list[TaskError], list[str]]:
        """检查外部依赖可用性"""
        errors = []
        warnings = []

        if request.task_type == TaskType.PDF_TO_NOTE:
            if not shutil.which("tesseract"):
                warnings.append(
                    "未检测到 tesseract，OCR 功能不可用。"
                    "安装: brew install tesseract (macOS) / apt install tesseract-ocr (Linux) / "
                    "从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装 (Windows)"
                )

        if request.task_type in (TaskType.VIDEO_TO_NOTE, TaskType.AUDIO_TO_NOTE):
            if not shutil.which("ffmpeg"):
                warnings.append(
                    "未检测到 ffmpeg，音频处理功能可能受限。"
                    "安装: brew install ffmpeg (macOS) / apt install ffmpeg (Linux) / "
                    "从 https://ffmpeg.org/download.html 下载并添加到 PATH (Windows)"
                )

        return errors, warnings
