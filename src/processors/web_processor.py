"""
网页转笔记处理器

处理流程：
1. 获取网页 HTML
2. 提取正文（去除导航、广告、侧边栏）
3. AI 总结生成结构化笔记
4. 断点续传支持
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .base import BaseProcessor
from ._lazy import httpx_module, bs4_module
from ..schemas.enums import ErrorCategory
from ..schemas.input import ConvertRequest, WebInput
from ..schemas.output import Result, TaskError, NoteOutput
from ..schemas.task import InternalTask
from ..core.checkpoint import CheckpointManager
from ..core.logging import get_logger

_logger = get_logger(__name__)


class WebProcessor(BaseProcessor):
    name = "web_processor"

    def __init__(self):
        # checkpoint 由 ProcessorFactory 注入
        self.checkpoint: CheckpointManager = CheckpointManager()

    async def process(
        self, task: InternalTask, request: ConvertRequest
    ) -> Result[NoteOutput]:
        inp = request.input
        if not isinstance(inp, WebInput):
            return Result.failure(TaskError(
                code="INVALID_INPUT_TYPE",
                message=f"期望 WebInput，实际为 {type(inp).__name__}",
                category=ErrorCategory.NON_RETRYABLE,
            ))

        # 幂等性检查
        if not request.force:
            cached = self.checkpoint.load(task.task_type, task.input_hash)
            if cached and cached.get("state") == "completed":
                return self._load_cached_result(cached)

        # 1. 获取网页内容（异常上浮到 Pipeline 统一处理）
        html = await self._fetch_page(inp.url)

        # 2. 提取正文
        if inp.extract_main_content:
            text, title, metadata = self._extract_main_content(html, inp.url)
        else:
            text, title, metadata = html, urlparse(inp.url).netloc, {}

        # 3. 生成笔记
        note = await self._generate_note(text, title, metadata, inp, request)

        # 4. 保存
        output_path = self._save_output(note, request.output)
        note.output_path = output_path

        self.checkpoint.mark_completed(
            task.task_type, task.input_hash, output_path, metadata
        )

        return Result.success(note)

    async def _fetch_page(self, url: str) -> str:
        """获取网页 HTML（懒加载 httpx，降级到 urllib）"""
        try:
            async with httpx_module.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
                resp.raise_for_status()
                return resp.text
        except ImportError:
            _logger.warning("httpx 不可用，降级使用 urllib（功能受限）")
            import urllib.request
            import urllib.error
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return resp.read().decode(charset, errors="replace")
            except urllib.error.URLError as e:
                raise RuntimeError(f"无法访问网页 {url}: {e}") from e

    def _extract_main_content(
        self, html: str, url: str
    ) -> tuple[str, str, dict]:
        """提取网页正文（懒加载 BeautifulSoup）

        使用 BeautifulSoup + 启发式算法去除导航、广告等。
        """
        try:
            soup = bs4_module.BeautifulSoup(html, "lxml")

            # 提取标题
            title = ""
            if soup.title:
                title = soup.title.get_text(strip=True)
            if not title:
                h1 = soup.find("h1")
                if h1:
                    title = h1.get_text(strip=True)
            if not title:
                title = urlparse(url).netloc

            # 移除无用元素
            for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                       "aside", "noscript", "iframe"]):
                tag.decompose()

            # 移除常见的广告/导航 class
            noise_classes = [
                "nav", "navbar", "sidebar", "footer", "header", "ad", "advertisement",
                "banner", "menu", "comment", "share", "social", "related",
            ]
            for tag in soup.find_all(class_=lambda c: c and any(
                nc in (c or "").lower() for nc in noise_classes
            )):
                tag.decompose()

            # 提取正文
            main_content = soup.find("article") or soup.find("main") or soup.find("body")
            if main_content is None:
                main_content = soup

            # 提取文本
            text_parts = []
            for tag in main_content.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                                "li", "blockquote", "pre", "td", "th"]):
                text = tag.get_text(strip=True)
                if text and len(text) > 5:
                    if tag.name.startswith("h"):
                        level = int(tag.name[1])
                        text_parts.append(f"{'#' * level} {text}")
                    elif tag.name == "li":
                        text_parts.append(f"- {text}")
                    elif tag.name == "blockquote":
                        text_parts.append(f"> {text}")
                    else:
                        text_parts.append(text)

            content = "\n\n".join(text_parts)

            metadata = {
                "source": url,
                "type": "webpage",
                "domain": urlparse(url).netloc,
                "title": title,
            }

            return content, title, metadata

        except ImportError:
            # 降级：简单文本提取
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text, urlparse(url).netloc, {"source": url, "type": "webpage"}

    async def _generate_note(
        self,
        text: str,
        title: str,
        metadata: dict,
        inp: WebInput,
        request: ConvertRequest,
    ) -> NoteOutput:
        """生成结构化笔记"""
        prompt = f"""
        请将以下网页内容转化为结构化笔记：

        要求：
        1. 保留原文标题层级
        2. 提取核心观点
        3. 标注来源 URL
        4. 关键数据使用表格/列表
        5. 末尾添加「一句话总结」

        网页内容：
        {text[:8000]}
        """

        content = self._format_content(text, title, metadata)

        return NoteOutput(
            title=title,
            content=content,
            output_path=Path("."),
            source_info=metadata,
        )

    def _format_content(self, text: str, title: str, metadata: dict) -> str:
        """格式化内容"""
        source_url = metadata.get("source", "")
        domain = metadata.get("domain", "")

        lines = [
            f"# {title}",
            "",
            f"> 来源: [{domain}]({source_url})",
            f"> 抓取时间: {__import__('datetime').datetime.now().isoformat()}",
            "",
            "---",
            "",
            text,
            "",
            "---",
            "",
            "## 一句话总结",
            "",
            "*(待 AI 生成)*",
        ]
        return "\n".join(lines)
