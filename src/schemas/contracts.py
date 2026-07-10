"""
数据契约定义（TypedDict）

锁定所有模块间传递的非结构化 dict 的字段类型。
所有 processor 间通信的 dict 必须符合此处定义的契约。

此模块零依赖（仅使用标准库 typing）。
"""
from __future__ import annotations

from typing import TypedDict


class ChunkMetadata(TypedDict, total=False):
    """InternalChunk.metadata 的契约"""
    pdf_path: str
    total_pages: int
    chunk_hash: str


class SOPStep(TypedDict):
    """SOP 步骤的契约"""
    number: str          # 步骤编号（"1" / "一"）
    content: str         # 步骤内容
    start_pos: int       # 在原文中的起始位置
    type: str            # "numbered_step" | "numbered_list" | "cn_numbered_list"


class SOPDecision(TypedDict):
    """SOP 决策点的契约"""
    type: str            # "condition" | "else_branch" | "loop"
    keyword: str         # 关键词（"如果"/"否则"/"重复"等）
    content: str         # 决策内容
    position: int        # 在原文中的位置
    context: str         # 上下文（前后各50字符）


class SOPDocument(TypedDict):
    """SOP 文档的契约（用于合并）"""
    title: str
    content: str
    output_path: str


class SourceInfo(TypedDict, total=False):
    """NoteOutput.source_info 的契约"""
    source: str          # 来源路径/URL
    type: str            # "pdf" | "youtube" | "bilibili" | "audio" | "webpage" | "sop"
    tags: list[str]
    step_count: int
    decision_count: int
    file_count: int
    domain: str
    video_id: str
    language: str
    format: str


class CheckpointData(TypedDict, total=False):
    """Checkpoint 文件的数据契约"""
    input_hash: str
    state: str           # TaskState 值的字符串
    output_path: str
    output_hash: str
    chunks_completed: list[int]
    error: str
    metadata: dict
    completed_at: str
    failed_at: str
    updated_at: str
    input_params: dict
    schema_version: int   # checkpoint 格式版本号


class MCPToolParams(TypedDict, total=False):
    """MCP 工具参数的契约"""
    path: str
    url: str
    source: str
    output_dir: str
    pages_per_chunk: int
    ocr_enabled: bool
    ocr_language: str
    ocr_direction: str
    language: str
    use_subtitles: bool
    extract_main: bool
    force: bool
