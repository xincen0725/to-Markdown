"""
PDF 文本提取器 —— 纯逻辑，零副作用

职责：
1. PDF 分块（按页拆分）
2. 文本提取（PyMuPDF）
3. 不负责：OCR、笔记生成、文件写入、checkpoint 管理
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ._lazy import fitz
from ..schemas.input import ChunkConfig
from ..schemas.task import InternalChunk


class PDFExtractor:
    """PDF 文本提取器——纯函数式，无副作用"""

    @staticmethod
    def split_pdf(
        pdf_path: Path,
        chunk_config: ChunkConfig,
        page_range: Optional[tuple[int, int]] = None,
    ) -> list[InternalChunk]:
        """将 PDF 拆分为 InternalChunk 列表"""
        chunks: list[InternalChunk] = []
        doc = None
        try:
            doc = fitz.open(str(pdf_path))
            total_pages = doc.page_count

            start_page, end_page = 1, total_pages
            if page_range:
                start_page, end_page = page_range
                start_page = max(1, start_page)
                end_page = min(total_pages, end_page)

            pages_per_chunk = chunk_config.pages_per_chunk
            overlap = chunk_config.overlap_pages

            chunk_index = 0
            current_start = start_page
            while current_start <= end_page:
                current_end = min(current_start + pages_per_chunk - 1, end_page)
                raw_texts = []
                for page_num in range(current_start - 1, current_end):
                    page = doc[page_num]
                    raw_texts.append(page.get_text("text", sort=True))

                chunk = InternalChunk(
                    index=chunk_index,
                    page_range=(current_start, current_end),
                    raw_text="\n\n".join(raw_texts),
                    metadata={
                        "pdf_path": str(pdf_path),
                        "total_pages": total_pages,
                    },
                )
                chunks.append(chunk)
                chunk_index += 1
                current_start = current_end + 1 - overlap

            return chunks
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def extract_text(pdf_path: Path, page_range: Optional[tuple[int, int]] = None) -> str:
        """提取 PDF 全部文本"""
        doc = None
        try:
            doc = fitz.open(str(pdf_path))
            total = doc.page_count
            start, end = 1, total
            if page_range:
                start, end = max(1, page_range[0]), min(total, page_range[1])

            texts = []
            for pn in range(start - 1, end):
                texts.append(doc[pn].get_text("text", sort=True))
            return "\n\n".join(texts)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass


class OCREngine:
    """OCR 引擎——纯逻辑，零副作用"""

    def __init__(self):
        from ._lazy import PIL_Image, pytesseract, numpy_module
        self.PIL = PIL_Image
        self.pytesseract = pytesseract
        self.np = numpy_module

    def ocr_chunk(
        self, chunk: InternalChunk, ocr_config,
    ) -> str:
        """对 chunk 执行 OCR 识别"""
        from ..schemas.enums import OCRDirection
        from ._lazy import fitz
        doc = None
        try:
            doc = fitz.open(chunk.metadata.get("pdf_path", ""))
            start, end = chunk.page_range

            ocr_texts = []
            for page_num in range(start - 1, end):
                page = doc[page_num]
                mat = fitz.Matrix(ocr_config.dpi / 72, ocr_config.dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                img = self.PIL.frombytes("RGB", [pix.width, pix.height], pix.samples)

                direction = ocr_config.direction
                if direction == OCRDirection.AUTO:
                    direction = self._detect_direction(img)

                if direction == OCRDirection.VERTICAL:
                    img = img.rotate(90, expand=True)

                text = self.pytesseract.image_to_string(
                    img, lang=ocr_config.language,
                    config=f"--dpi {ocr_config.dpi}",
                )
                ocr_texts.append(text)

            return "\n\n".join(ocr_texts)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _detect_direction(self, img) -> "OCRDirection":
        """自动判断文字方向"""
        from ..schemas.enums import OCRDirection
        img_array = self.np.array(img.convert("L"))
        h_proj = self.np.mean(img_array, axis=0)
        v_proj = self.np.mean(img_array, axis=1)
        h_var = self.np.var(h_proj)
        v_var = self.np.var(v_proj)
        if v_var > h_var * 1.5:
            return OCRDirection.VERTICAL
        return OCRDirection.HORIZONTAL
