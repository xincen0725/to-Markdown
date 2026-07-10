"""
Bilibili API 集成

提供 B 站视频信息获取、字幕下载等功能。
"""
from __future__ import annotations

import re
from typing import Optional


class BilibiliClient:
    """Bilibili API 客户端"""

    API_BASE = "https://api.bilibili.com"

    @staticmethod
    def extract_bvid(url: str) -> Optional[str]:
        """从 URL 提取 BV 号"""
        match = re.search(r'(BV[a-zA-Z0-9]{10})', url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def extract_aid(url: str) -> Optional[str]:
        """从 URL 提取 AV 号"""
        match = re.search(r'av(\d+)', url, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """检查是否是有效的 Bilibili URL"""
        return bool(
            BilibiliClient.extract_bvid(url) or
            BilibiliClient.extract_aid(url) or
            "bilibili.com" in url
        )

    @staticmethod
    def get_video_info_url(bvid: str) -> str:
        """获取视频信息 API URL"""
        return f"{BilibiliClient.API_BASE}/x/web-interface/view?bvid={bvid}"

    @staticmethod
    def get_subtitle_url(cid: int) -> str:
        """获取字幕列表 API URL"""
        return f"{BilibiliClient.API_BASE}/x/player/v2?cid={cid}"
