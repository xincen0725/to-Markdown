"""
OCR 集成模块

支持：
- 繁体中文竖排文字识别
- 自动判断文字方向
- 多语言 OCR

所有重型库通过懒加载引入。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.logging import get_logger
from ..schemas.enums import OCRDirection
from ..processors._lazy import PIL_Image, pytesseract, fitz, numpy_module

_logger = get_logger(__name__)


class OCREngine:
    """OCR 引擎封装"""

    def __init__(
        self,
        language: str = "chi_tra+chi_sim+eng",
        dpi: int = 300,
        direction: OCRDirection = OCRDirection.AUTO,
    ):
        self.language = language
        self.dpi = dpi
        self.direction = direction

    def extract_text(
        self,
        image_path: Path,
        direction: Optional[OCRDirection] = None,
    ) -> str:
        """从图像提取文字

        Args:
            image_path: 图像路径
            direction: 文字方向（覆盖实例默认值）

        Returns:
            提取的文字
        """
        try:
            img = PIL_Image.open(image_path)
            direction = direction or self.direction

            # 预处理
            img = self._preprocess(img)

            # 自动判断方向
            if direction == OCRDirection.AUTO:
                direction = self._detect_direction(img)

            # 竖排文字旋转
            if direction == OCRDirection.VERTICAL:
                img = img.rotate(90, expand=True)

            # OCR
            text = pytesseract.image_to_string(
                img,
                lang=self.language,
                config=f"--dpi {self.dpi} -c preserve_interword_spaces=1",
            )

            return text.strip()

        except ImportError:
            raise ImportError(
                "需要 pytesseract 和 Pillow。安装: pip install pytesseract Pillow"
            )

    def extract_text_from_pdf_page(
        self,
        pdf_path: Path,
        page_num: int,
        direction: Optional[OCRDirection] = None,
    ) -> str:
        """从 PDF 单页提取文字（渲染为图像后 OCR，懒加载）"""
        doc = None
        tmp_path = None
        try:
            import io

            doc = fitz.open(str(pdf_path))
            if page_num < 0 or page_num >= doc.page_count:
                return ""

            page = doc[page_num]
            mat = fitz.Matrix(self.dpi / 72, self.dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img = PIL_Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # 保存为临时文件
            tmp_path = pdf_path.parent / f"_ocr_tmp_{page_num}.png"
            img.save(str(tmp_path))

            text = self.extract_text(tmp_path, direction)
            return text

        except ImportError:
            _logger.warning("依赖不可用，无法对 %s 第%d页执行 OCR", pdf_path.name, page_num + 1)
            return ""
        except Exception as e:
            _logger.warning("处理失败 (%s 第%d页): %s", pdf_path.name, page_num + 1, e)
            return ""
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _preprocess(self, img):
        """图像预处理（懒加载 numpy）"""
        np = numpy_module

        if img.mode != "L":
            img = img.convert("L")

        img = PIL_Image.ImageOps.autocontrast(img, cutoff=5)
        img = img.filter(PIL_Image.ImageFilter.MedianFilter(size=3))

        img_array = np.array(img)
        threshold = np.mean(img_array)
        img_array = np.where(img_array > threshold, 255, 0).astype(np.uint8)
        img = PIL_Image.fromarray(img_array)

        return img

    def _detect_direction(self, img) -> OCRDirection:
        """自动检测文字方向"""
        np = numpy_module

        img_array = np.array(img)
        h_proj = np.mean(img_array, axis=1)
        v_proj = np.mean(img_array, axis=0)
        h_var = np.var(h_proj)
        v_var = np.var(v_proj)

        if v_var > h_var * 1.5:
            return OCRDirection.VERTICAL
        return OCRDirection.HORIZONTAL
