"""
自举依赖管理器

设计目标：
1. 用户无需手动 pip install——首次运行时自动安装缺失依赖
2. 懒加载：按功能模块分组建模，只在实际使用时才检查/安装
3. 虚拟环境支持：优先使用当前 Python 环境，不创建新 venv
4. 幂等性：重复安装无副作用
5. 进度反馈：安装时显示进度，不让用户以为卡死
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .core.logging import get_logger

_logger = get_logger(__name__)


@dataclass
class DepGroup:
    """依赖组"""
    name: str
    description: str
    packages: list[str]
    optional: bool = False
    _checked: bool = field(default=False, init=False)
    _installed: bool = field(default=False, init=False)

    def is_available(self) -> bool:
        """检查是否所有包都可导入"""
        for pkg in self.packages:
            # 包名可能带版本约束，取包名部分
            pkg_name = pkg.split(">=")[0].split("==")[0].split("<")[0].strip()
            # 映射：PyPI 名 → import 名
            import_name = _IMPORT_NAME_MAP.get(pkg_name, pkg_name.replace("-", "_"))
            try:
                importlib.import_module(import_name)
            except ImportError:
                return False
        return True

    def install(self) -> bool:
        """安装依赖组，返回是否成功"""
        if self.is_available():
            self._checked = True
            self._installed = True
            return True

        _logger.info("正在安装 %s 依赖: %s", self.name, ", ".join(self.packages))

        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet"] + self.packages,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self.is_available():
                self._checked = True
                self._installed = True
                _logger.info("%s 依赖安装成功", self.name)
                return True
            else:
                _logger.warning("%s 安装后仍无法导入，请手动安装", self.name)
                return False
        except subprocess.CalledProcessError as e:
            _logger.error("%s 安装失败: %s", self.name, e)
            return False

    def ensure(self) -> None:
        """确保依赖可用，否则抛出 ImportError"""
        if self._checked and self._installed:
            return
        if self.is_available():
            self._checked = True
            self._installed = True
            return
        if not self.install():
            raise ImportError(
                f"无法安装 {self.name} 依赖。请手动执行: pip install {' '.join(self.packages)}"
            )


# PyPI 包名 → Python import 名映射
_IMPORT_NAME_MAP: dict[str, str] = {
    "pymupdf": "fitz",
    "beautifulsoup4": "bs4",
    "yt-dlp": "yt_dlp",
    "opencv-python": "cv2",
    "python-multipart": "multipart",
    "Pillow": "PIL",
}


# ─── 依赖组定义 ───

DEP_CORE = DepGroup(
    name="核心",
    description="状态机、断点续传、Schema 校验——零依赖",
    packages=[],  # 纯标准库，无需安装
)

DEP_PDF = DepGroup(
    name="PDF",
    description="PDF 解析与渲染",
    packages=["pymupdf>=1.23.0"],
)

DEP_OCR = DepGroup(
    name="OCR",
    description="OCR 文字识别（繁体竖排支持）",
    packages=["pytesseract>=0.3.10", "opencv-python>=4.8.0", "Pillow>=10.0.0"],
    optional=True,
)

DEP_VIDEO = DepGroup(
    name="视频",
    description="视频下载与处理",
    packages=["yt-dlp>=2023.0.0"],
)

DEP_WEB = DepGroup(
    name="网页",
    description="网页抓取与解析",
    packages=["httpx>=0.25.0", "beautifulsoup4>=4.12.0", "lxml>=4.9.0"],
)

DEP_AI = DepGroup(
    name="AI",
    description="AI 总结与转录（Whisper + GPT）",
    packages=["openai>=1.0.0"],
    optional=True,
)

DEP_CLI = DepGroup(
    name="CLI",
    description="命令行界面",
    packages=["typer>=0.9.0", "rich>=13.0.0"],
)

DEP_MCP = DepGroup(
    name="MCP",
    description="MCP Server 协议",
    packages=["mcp>=1.0.0"],
    optional=True,
)

DEP_ALL = DepGroup(
    name="全部",
    description="所有功能依赖",
    packages=[
        "pydantic>=2.0.0",
        "pymupdf>=1.23.0",
        "httpx>=0.25.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=4.9.0",
        "Pillow>=10.0.0",
        "yt-dlp>=2023.0.0",
        "openai>=1.0.0",
        "typer>=0.9.0",
        "rich>=13.0.0",
    ],
)

# 任务类型 → 需要的依赖组映射
TASK_DEPS_MAP = {
    "pdf_to_note": [DEP_PDF],
    "sop_extract": [DEP_PDF],
    "video_to_note": [DEP_VIDEO, DEP_AI],
    "audio_to_note": [DEP_AI],
    "web_to_note": [DEP_WEB],
}


def ensure_deps_for_task(task_type: str) -> None:
    """确保某类任务所需的依赖已安装"""
    groups = TASK_DEPS_MAP.get(task_type, [])
    for group in groups:
        group.ensure()


def ensure_cli_deps() -> None:
    """确保 CLI 依赖"""
    DEP_CLI.ensure()


def ensure_all_optional() -> list[str]:
    """尝试安装所有可选依赖，返回失败的列表"""
    failed = []
    for dep in [DEP_OCR, DEP_AI, DEP_MCP]:
        try:
            dep.ensure()
        except ImportError:
            failed.append(dep.name)
    return failed


def check_environment() -> dict:
    """检查当前环境依赖状态"""
    all_groups = [DEP_CORE, DEP_PDF, DEP_OCR, DEP_VIDEO, DEP_WEB, DEP_AI, DEP_CLI, DEP_MCP]
    return {
        group.name: {
            "available": group.is_available(),
            "optional": group.optional,
            "packages": group.packages,
        }
        for group in all_groups
    }
