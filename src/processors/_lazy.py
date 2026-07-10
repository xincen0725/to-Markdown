"""
处理器懒加载辅助

所有重型库通过此模块懒加载，确保：
1. 首次使用时自动安装依赖
2. 安装失败给出明确的错误提示
3. 核心架构（schema/core）零依赖可直接导入

设计要点：
- 自包含：不依赖 bootstrap 模块，避免隐式依赖链
- 安装规则内联定义，模块间无交叉引用
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any

from ..core.logging import get_logger

_logger = get_logger(__name__)

# ─── 内联依赖映射（自包含，不依赖 bootstrap）───

_DEP_PACKAGES: dict[str, list[str]] = {
    "fitz":       ["pymupdf>=1.23.0"],
    "pymupdf":    ["pymupdf>=1.23.0"],
    "PIL":        ["Pillow>=10.0.0"],
    "PIL.Image":  ["Pillow>=10.0.0"],
    "pytesseract": ["pytesseract>=0.3.10"],
    "cv2":        ["opencv-python>=4.8.0"],
    "numpy":      ["numpy>=1.24.0"],
    "yt_dlp":     ["yt-dlp>=2023.0.0"],
    "httpx":      ["httpx>=0.25.0"],
    "bs4":        ["beautifulsoup4>=4.12.0", "lxml>=4.9.0"],
    "lxml":       ["lxml>=4.9.0"],
    "openai":     ["openai>=1.0.0"],
}


def _install_package(packages: list[str]) -> bool:
    """静默安装 pip 包，返回是否成功"""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + packages,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


class LazyImport:
    """懒导入包装器——自包含，零外部依赖

    用法：
        fitz = LazyImport("fitz")
        doc = fitz.open(path)  # 首次访问时自动安装并导入
    """

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module: Any = None
        self._loaded = False

    def _load(self) -> Any:
        if not self._loaded:
            try:
                self._module = importlib.import_module(self._module_name)
            except ImportError:
                # 自包含安装逻辑：查找内联映射 → pip install → 重试导入
                packages = _DEP_PACKAGES.get(self._module_name)
                if packages:
                    _logger.info("自动安装依赖: %s", " ".join(packages))
                    if _install_package(packages):
                        self._module = importlib.import_module(self._module_name)
                    else:
                        raise ImportError(
                            f"无法自动安装 {self._module_name}。"
                            f"请手动执行: pip install {' '.join(packages)}"
                        ) from None
                else:
                    raise ImportError(
                        f"缺少依赖: {self._module_name}。"
                        f"请手动安装或运行 pip install to-markdown[all]"
                    ) from None
            self._loaded = True
        return self._module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)

    def __call__(self, *args, **kwargs) -> Any:
        return self._load()(*args, **kwargs)


# ─── 预定义懒导入对象 ───

fitz = LazyImport("fitz")              # PyMuPDF
PIL_Image = LazyImport("PIL.Image")    # Pillow
pytesseract = LazyImport("pytesseract") # Tesseract OCR
numpy_module = LazyImport("numpy")      # NumPy
yt_dlp_module = LazyImport("yt_dlp")    # yt-dlp
httpx_module = LazyImport("httpx")      # HTTPX
bs4_module = LazyImport("bs4")          # BeautifulSoup4
openai_module = LazyImport("openai")    # OpenAI
