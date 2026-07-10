"""
YouTube API 集成

提供 YouTube 视频信息获取、字幕下载等功能。
"""
from __future__ import annotations

import re
from typing import Optional


class YouTubeClient:
    """YouTube API 客户端"""

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """从 URL 提取视频 ID"""
        patterns = [
            r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """检查是否是有效的 YouTube URL"""
        return bool(YouTubeClient.extract_video_id(url))

    @staticmethod
    def get_thumbnail_url(video_id: str, quality: str = "maxresdefault") -> str:
        """获取缩略图 URL"""
        return f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"

    @staticmethod
    def get_embed_url(video_id: str) -> str:
        """获取嵌入 URL"""
        return f"https://www.youtube.com/embed/{video_id}"
